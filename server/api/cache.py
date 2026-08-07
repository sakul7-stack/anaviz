"""Cache-aside hydration: fetch missing ranges from archive, refresh rollups."""
from __future__ import annotations

import asyncio
import io
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg
from psycopg_pool import AsyncConnectionPool

from .archive import ArchiveClient

ROLLUPS = ["eh_1m", "eh_5m", "eh_1h", "eh_1d"]

ROLLUP_BUCKETS = {
    "eh_1m": 60,
    "eh_5m": 300,
    "eh_1h": 3600,
    "eh_1d": 86400,
}


async def ensure_cache_table(cur: psycopg.AsyncCursor) -> None:
    await cur.execute(
        "CREATE TABLE IF NOT EXISTS cache_coverage ("
        "element_id INTEGER PRIMARY KEY, "
        "t_min TIMESTAMPTZ NOT NULL, "
        "t_max TIMESTAMPTZ NOT NULL)")


async def fetch_and_cache_multi(
    archive: ArchiveClient,
    cur: psycopg.AsyncCursor,
    element_ids: list[int],
    start: datetime,
    end: datetime,
) -> tuple[dict[int, list[tuple[datetime, datetime]]], dict[int, str]]:
    """Fetch missing ranges from the archive into eventhistory for several
    elements in one pass. Returns (intervals_fetched_per_element, errors)."""
    await cur.execute(
        "SELECT element_id, t_min, t_max FROM cache_coverage "
        "WHERE element_id = ANY(%s)", (element_ids,))
    coverages = {eid: (t_min, t_max) for eid, t_min, t_max in await cur.fetchall()}

    tasks: list[tuple[int, tuple[datetime, datetime]]] = []
    for eid in element_ids:
        cov = coverages.get(eid)
        gaps: list[tuple[datetime, datetime]] = []
        if cov is None:
            gaps.append((start, end))
        elif end <= cov[0] or start >= cov[1]:
            gaps.append((start, end))
        else:
            if start < cov[0]:
                gaps.append((start, cov[0]))
            if end > cov[1]:
                gaps.append((cov[1], end))
        probed = _PROBED_EMPTY.get(eid)
        if probed:
            gaps = [g for gap in gaps for g in _subtract_probed(*gap, probed)]
        tasks.extend((eid, (a, b)) for a, b in gaps)

    if not tasks:
        return {}, {}

    ext = await get_archive_extent(archive)
    if ext is not None:
        tasks = [(eid, (a, b)) for eid, (a, b) in tasks
                 for (a, b) in _clip_to_extent([(a, b)], ext)]
    if not tasks:
        return {}, {}

    datas = await asyncio.gather(*(
        _fetch_csv(archive, eid, a, b) for eid, (a, b) in tasks),
        return_exceptions=True)

    fetched: dict[int, list[tuple[datetime, datetime]]] = {}
    errors: dict[int, str] = {}
    buf = io.StringIO()
    upserts: list[tuple[int, datetime, datetime]] = []

    for (eid, (a, b)), data in zip(tasks, datas):
        if isinstance(data, BaseException):
            errors.setdefault(eid, str(data))
            continue
        if data is None:
            _record_probed_empty(eid, a, b)
            continue
        fetched.setdefault(eid, []).append((a, b))
        timestamps, values, quality_flags = data
        t_min, t_max = timestamps.min(), timestamps.max()
        if t_min > a.timestamp():
            _record_probed_empty(eid, a, datetime.fromtimestamp(t_min, tz=timezone.utc))
        if t_max < b.timestamp():
            _record_probed_empty(eid, datetime.fromtimestamp(t_max, tz=timezone.utc), b)
        for i in range(len(timestamps)):
            ts = datetime.fromtimestamp(timestamps[i], tz=timezone.utc).isoformat()
            buf.write(f"{eid},{ts},{values[i]},{quality_flags[i]}\n")
        upserts.append((
            eid,
            datetime.fromtimestamp(t_min, tz=timezone.utc),
            datetime.fromtimestamp(t_max, tz=timezone.utc)))

    if upserts:
        buf.seek(0)
        async with cur.copy(
                "COPY eventhistory (element_id, ts, value, quality_flag) "
                "FROM STDIN WITH (FORMAT csv)") as copy_ctx:
            while chunk := buf.read(1 << 20):
                await copy_ctx.write(chunk.encode())
        placeholders = ", ".join("(%s, %s, %s)" for _ in upserts)
        await cur.execute(
            "INSERT INTO cache_coverage (element_id, t_min, t_max) VALUES "
            + placeholders + " "
            "ON CONFLICT (element_id) DO UPDATE SET "
            "t_min = LEAST(cache_coverage.t_min, EXCLUDED.t_min), "
            "t_max = GREATEST(cache_coverage.t_max, EXCLUDED.t_max)",
            [x for u in upserts for x in u])
    return fetched, errors


_REFRESH_SEM = asyncio.Semaphore(2)
_REFRESH_INFLIGHT: set[str] = set()
READAHEAD_CAP_DAYS = 30
_READAHEAD_SEM = asyncio.Semaphore(1)
_READAHEAD_INFLIGHT: set[int] = set()
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def drain_background() -> None:
    if _BACKGROUND_TASKS:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)


_EXTENT_TTL_S = 300
_EXTENT_CACHE: tuple[tuple[datetime, datetime] | None, datetime] = (
    None, datetime.min.replace(tzinfo=timezone.utc))

_FETCH_SEM = asyncio.Semaphore(4)


async def get_archive_extent(archive: ArchiveClient) -> tuple[datetime, datetime] | None:
    global _EXTENT_CACHE
    ext, at = _EXTENT_CACHE
    if ext is None or (datetime.now(timezone.utc) - at).total_seconds() > _EXTENT_TTL_S:
        try:
            e = await archive.extent()
            ext = (datetime.fromtimestamp(e.t_min, tz=timezone.utc),
                   datetime.fromtimestamp(e.t_max, tz=timezone.utc))
        except Exception:
            return ext
        _EXTENT_CACHE = (ext, datetime.now(timezone.utc))
    return ext


def _clip_to_extent(intervals: list[tuple[datetime, datetime]],
                    ext: tuple[datetime, datetime]) -> list[tuple[datetime, datetime]]:
    t_min, t_max = ext
    return [(max(a, t_min), min(b, t_max))
            for a, b in intervals if min(b, t_max) > max(a, t_min)]


async def _fetch_csv(archive: ArchiveClient, eid: int, a: datetime, b: datetime):
    async with _FETCH_SEM:
        return await archive.eventhistory_csv(eid, a.timestamp(), b.timestamp())


_PROBED_EMPTY: dict[int, list[tuple[datetime, datetime]]] = {}
_PROBED_EMPTY_CAP = 8


def _record_probed_empty(eid: int, a: datetime, b: datetime) -> None:
    intervals = _PROBED_EMPTY.setdefault(eid, [])
    merged = []
    for x, y in intervals:
        if b >= x and a <= y:
            a, b = min(a, x), max(b, y)
        else:
            merged.append((x, y))
    merged.append((a, b))
    _PROBED_EMPTY[eid] = merged[-_PROBED_EMPTY_CAP:]


def _subtract_probed(a: datetime, b: datetime,
                     probed: list[tuple[datetime, datetime]]
                     ) -> list[tuple[datetime, datetime]]:
    pts = sorted(set([a, b] + [
        p for x, y in probed if y > a and x < b for p in (x, y) if a <= p <= b]))
    gaps = []
    for lo, hi in zip(pts, pts[1:]):
        if not any(x <= lo and hi <= y for x, y in probed):
            gaps.append((lo, hi))
    return gaps


def reset_probed_empty() -> None:
    _PROBED_EMPTY.clear()


def readahead_intervals(start: datetime, end: datetime,
                        margin: timedelta) -> list[tuple[datetime, datetime]]:
    pairs = [(start - margin, start), (end, end + margin)]
    return [(a, b) for a, b in pairs if b > a]


async def readahead(pool: AsyncConnectionPool, archive: ArchiveClient,
                    element_id: int, start: datetime, end: datetime,
                    margin: timedelta) -> None:
    if element_id in _READAHEAD_INFLIGHT:
        return
    _READAHEAD_INFLIGHT.add(element_id)
    try:
        for a, b in readahead_intervals(start, end, margin):
            try:
                async with _READAHEAD_SEM:
                    async with pool.connection(timeout=10) as conn:
                        async with conn.transaction():
                            fetched, errors = await fetch_and_cache_multi(
                                archive, conn.cursor(), [element_id], a, b)
                if element_id in fetched:
                    spawn_background(refresh_rollups(pool, fetched[element_id]))
            except Exception as e:
                print(f"  readahead {element_id}: {e}")
    finally:
        _READAHEAD_INFLIGHT.discard(element_id)


async def refresh_rollups(pool: AsyncConnectionPool,
                          intervals: list[tuple[datetime, datetime]]) -> None:
    tasks = []
    for t_min, t_max in intervals:
        span = (t_max - t_min).total_seconds()
        for view in ROLLUPS:
            if ROLLUP_BUCKETS.get(view, 0) > span:
                continue
            tasks.append(asyncio.create_task(_refresh_view(pool, view, t_min, t_max)))
    if not tasks:
        return
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"rollup refresh error: {e}")


async def _refresh_view(pool: AsyncConnectionPool, view: str,
                        t_min: datetime, t_max: datetime) -> None:
    async with _REFRESH_SEM:
        _REFRESH_INFLIGHT.add(view)
        try:
            async with pool.connection(timeout=10) as conn:
                await conn.set_autocommit(True)
                cur = conn.cursor()
                t0 = time.perf_counter()
                await cur.execute(
                    "CALL refresh_continuous_aggregate(%s::regclass, %s, %s)",
                    (view, t_min, t_max))
                print(f"  refreshed {view} in {(time.perf_counter()-t0)*1000:.0f}ms")
        except Exception as e:
            print(f"  skip {view}: {e}")
        finally:
            _REFRESH_INFLIGHT.discard(view)


def refresh_status() -> list[str]:
    """Views currently being refreshed (for the frontend refresh indicator)."""
    return sorted(_REFRESH_INFLIGHT)
