"""FastAPI server — 10 REST endpoints + static frontend."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg_pool import AsyncConnectionPool

from .api.archive import ArchiveClient, archive_from_settings
from .api.cache import (
    READAHEAD_CAP_DAYS,
    drain_background,
    ensure_cache_table,
    fetch_and_cache_multi,
    get_archive_extent,
    readahead,
    refresh_rollups,
    reset_probed_empty,
    refresh_status,
    spawn_background,
)
from .config import Settings
from .downsample import downsample
from .downsample.resolution import RAW_ROW_CAP, select_resolution

settings = Settings.from_env()

pool: AsyncConnectionPool | None = None
archive: ArchiveClient | None = None


def parse_ts(s: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(s), tz=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, f"bad timestamp: {s!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool, archive
    pool = AsyncConnectionPool(settings.db_url, min_size=1, max_size=8, open=False)
    await pool.open()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await ensure_cache_table(cur)
    archive = archive_from_settings(settings)
    await get_archive_extent(archive)
    yield
    await pool.close()
    await archive.aclose()


app = FastAPI(title="anaviz", lifespan=lifespan)


# ── Helper functions ──────────────────────────────────────────────────────────

def _group_by_element(rows: list[tuple]) -> dict[int, list[tuple]]:
    grouped: dict[int, list[tuple]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row[1:])
    return grouped


def _rollup_payload(rows: list[tuple]) -> dict | None:
    if not rows:
        return None
    (bucket, v_min, v_max, v_avg, _,
     v_first, v_last, min_t, max_t, first_t, last_t) = zip(*rows)
    return {
        "t": np.array(bucket), "min": np.array(v_min), "max": np.array(v_max),
        "avg": np.array(v_avg), "first": np.array(v_first), "last": np.array(v_last),
        "min_t": np.array(min_t), "max_t": np.array(max_t),
        "first_t": np.array(first_t), "last_t": np.array(last_t),
    }


def _raw_payload(rows: list[tuple[float, float]], cap: int) -> tuple[dict, bool, int]:
    if not rows:
        return {"t": np.array([]), "avg": np.array([])}, False, 0
    t_arr = np.array([r[0] for r in rows])
    v_arr = np.array([r[1] for r in rows])
    return {"t": t_arr, "avg": v_arr}, len(rows) > cap, len(rows)


async def _fetch_raw_rows(
    conn, element_ids: list[int], start: datetime, end: datetime
) -> tuple[list[tuple], dict[int, int]]:
    if not element_ids:
        return [], {}
    cur = await conn.execute(
        "SELECT element_id, extract(epoch FROM ts)::float8, value, total "
        "FROM (SELECT element_id, ts, value, ROW_NUMBER() OVER "
        "  (PARTITION BY element_id ORDER BY ts DESC) AS rn, "
        "  COUNT(*) OVER (PARTITION BY element_id) AS total "
        "  FROM eventhistory WHERE element_id = ANY(%s) "
        "  AND ts>=%s AND ts<%s) q WHERE q.rn <= %s "
        "ORDER BY element_id, ts",
        (element_ids, start, end, RAW_ROW_CAP + 1))
    rows = await cur.fetchall()
    return rows, {eid: total for eid, _, _, total in rows}


def _group_raw(rows: list[tuple]) -> dict[int, list[tuple[float, float]]]:
    return {eid: [(ts, val) for ts, val, _ in per_eid]
            for eid, per_eid in _group_by_element(rows).items()}


async def _query_series_multi(
    conn, element_ids: list[int], start: datetime, end: datetime, res,
) -> tuple[dict[int, tuple[dict, bool, int]], dict[int, int]]:
    if res.is_raw:
        rows, counts = await _fetch_raw_rows(conn, element_ids, start, end)
        return ({eid: _raw_payload(_group_raw(rows).get(eid, []), RAW_ROW_CAP)
                 for eid in element_ids}, counts)

    cur = await conn.execute(
        f"SELECT element_id, extract(epoch FROM bucket)::float8, "
        f"v_min, v_max, v_avg, n, v_first, v_last, "
        f"extract(epoch FROM min_ts)::float8, "
        f"extract(epoch FROM max_ts)::float8, "
        f"extract(epoch FROM first_ts)::float8, "
        f"extract(epoch FROM last_ts)::float8 "
        f"FROM {res.table} WHERE element_id = ANY(%s) "
        f"AND bucket>=%s AND bucket<%s ORDER BY element_id, bucket",
        (element_ids, start, end))
    grouped = _group_by_element(await cur.fetchall())

    results: dict[int, tuple[dict, bool, int]] = {}
    counts: dict[int, int] = {}
    fallback: list[int] = []
    for eid in element_ids:
        rows = grouped.get(eid, [])
        payload = _rollup_payload(rows)
        if payload is None:
            fallback.append(eid)
        else:
            results[eid] = (payload, False, len(payload["t"]))
            counts[eid] = int(sum(r[4] for r in rows))

    if fallback:
        rows, raw_counts = await _fetch_raw_rows(conn, fallback, start, end)
        grouped = _group_raw(rows)
        for eid in fallback:
            results[eid] = _raw_payload(grouped.get(eid, []), RAW_ROW_CAP)
            counts[eid] = raw_counts.get(eid, 0)
    return results, counts


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/extent")
async def extent():
    result = await archive.extent()
    return {"t_min": result.t_min, "t_max": result.t_max}


@app.get("/api/channels")
async def channels():
    rows = await archive.channels()
    return [{"element_id": c.element_id, "name": c.name} for c in rows]


@app.get("/api/series")
async def series(
    t0: str, t1: str,
    element_id: list[int] = Query(min_length=1, max_length=64),
    px: int = Query(default=1000, ge=50, le=8000),
    k: int = Query(default=2, ge=1, le=4),
    algo: str = Query(default="M4", pattern="^(LTTB|M4|MINMAXLTTB|RAW)$"),
):
    start, end = parse_ts(t0), parse_ts(t1)
    span = (end - start).total_seconds()
    if span <= 0:
        raise HTTPException(400, "t1 must be after t0")

    budget = px * k
    resolution = select_resolution(span, px)
    query_start = time.perf_counter()

    async with pool.connection() as conn:
        async with conn.transaction():
            archive_start = time.perf_counter()
            fetched, errors = await fetch_and_cache_multi(
                archive, conn.cursor(), element_id, start, end)
            archive_ms = (time.perf_counter() - archive_start) * 1000
    all_intervals = [iv for intervals in fetched.values() for iv in intervals]
    if all_intervals:
        await refresh_rollups(pool, all_intervals)

    ok_ids = [eid for eid in element_id if eid not in errors]
    async with pool.connection() as conn:
        payloads, raw_counts = await _query_series_multi(conn, ok_ids, start, end, resolution)

    margin = timedelta(seconds=min(span, READAHEAD_CAP_DAYS * 86400))
    results = []
    for eid in element_id:
        if eid in errors:
            results.append({"element_id": eid, "error": errors[eid]})
            continue
        spawn_background(readahead(pool, archive, eid, start, end, margin))
        payload, truncated, n_scanned = payloads[eid]
        payload, downsample_ms = downsample(payload, algo, budget)
        out = {key: np.round(np.array(arr), 6).tolist()
               for key, arr in payload.items()}
        results.append({
            "element_id": eid,
            "resolution": resolution.name,
            "algo": algo,
            "rows_raw": raw_counts.get(eid, 0),
            "rows_scanned": n_scanned,
            "rows_returned": len(out["t"]),
            "truncated": truncated,
            "query_ms": round((time.perf_counter() - query_start) * 1000, 1),
            "archive_ms": round(archive_ms, 1),
            "ds_ms": round(downsample_ms, 2),
            "series": out,
        })
    return results


@app.get("/api/transitions")
async def transitions(t0: str, t1: str, system_id: int | None = None):
    start, end = parse_ts(t0), parse_ts(t1)
    rows = await archive.transitions(start.timestamp(), end.timestamp(), system_id)
    return [
        {
            "transition_id": r.transition_id,
            "system_id": r.system_id,
            "old": r.old,
            "new": r.new,
            "ts": r.ts.timestamp(),
            "operator_id": r.operator_id,
        }
        for r in rows
    ]


@app.get("/api/anomaly-score")
async def anomaly_score(
    element_id: int,
    t0: str,
    t1: str,
    train_span: int = Query(default=3600, ge=300, le=86400),
):
    """Per-timestep SPOT anomaly score for a channel in [t0, t1]."""
    from .api.spot import SPOT

    start, end = parse_ts(t0), parse_ts(t1)
    train_start = start - timedelta(seconds=train_span)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT extract(epoch FROM ts)::float8, value "
            "FROM eventhistory WHERE element_id=%s AND ts>=%s AND ts<%s ORDER BY ts",
            (element_id, train_start, start),
        )
        train_rows = await cur.fetchall()
        cur = await conn.execute(
            "SELECT extract(epoch FROM ts)::float8, value "
            "FROM eventhistory WHERE element_id=%s AND ts>=%s AND ts<%s ORDER BY ts",
            (element_id, start, end),
        )
        test_rows = await cur.fetchall()

    if not train_rows or not test_rows:
        return {"element_id": element_id, "t": [], "score": [], "threshold": None}

    train_t, train_v = zip(*train_rows)
    test_t, test_v = zip(*test_rows)
    spot = SPOT()
    threshold = spot.fit(np.array(train_v, dtype=float))
    scores = spot.score(np.array(test_v, dtype=float))
    return {
        "element_id": element_id,
        "t": list(test_t),
        "score": np.round(scores, 4).tolist(),
        "threshold": round(float(threshold), 4),
    }


@app.get("/api/heatmap")
async def heatmap_endpoint(
    t0: str,
    t1: str,
    px: int = Query(default=200, ge=10, le=2000),
    channel_ids: str | None = None,
):
    """Z-score heatmap grid for multiple channels over [t0, t1]."""
    from .api.heatmap import build_heatmap

    start, end = parse_ts(t0), parse_ts(t1)
    if channel_ids:
        ids = [int(x) for x in channel_ids.split(",") if x.strip()]
    else:
        rows = await archive.channels()
        ids = [c.element_id for c in rows]
    if len(ids) > 200:                       # keep the grid readable / query bounded
        ids = ids[:200]
    return await build_heatmap(pool, ids, start, end, px)


@app.get("/api/status")
async def status():
    """Which rollup views are currently being refreshed."""
    return {"refreshing": refresh_status()}


@app.get("/api/clear-cache")
async def clear_cache():
    await drain_background()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "TRUNCATE eventhistory, transitions, "
                "cache_coverage, eh_1m, eh_5m, eh_1h, eh_1d "
                "CASCADE")
    reset_probed_empty()
    return {"ok": True}


# ── Static frontend ───────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(settings.frontend_path)), name="frontend")


@app.get("/")
async def index():
    return FileResponse(settings.frontend_path / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)
