"""Datasource archive server — serves the real HLT data as the /archive/* API.

This is the "source of truth" container (port 9000). The viz app (project
container) hydrates its local cache from here over HTTP, so its own
`eventhistory` is just a cache and clear-cache can never destroy the real data.

Schema: the datasource DB holds the same tables as the project cache
(eventhistory, hardware_mapping, transitions, subsystems). No rollups
are needed here — the archive serves raw rows; the project builds and refreshes
rollups on demand.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from psycopg_pool import AsyncConnectionPool

DB_URL = os.environ.get("DCSVIZ_DB", "postgresql://dcs:dcs@localhost:5432/dcs")
PAGE_LIMIT = 50_000

pool: AsyncConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = AsyncConnectionPool(DB_URL, min_size=1, max_size=8, open=False)
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="anaviz-datasource", lifespan=lifespan)


def parse_ts(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s)
        return dt.timestamp() if dt.tzinfo else dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        raise HTTPException(400, f"bad timestamp: {s!r}")


@app.get("/archive/channels")
async def channels():
    async with pool.connection() as conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT element_id, full_name FROM hardware_mapping ORDER BY element_id")
        rows = await cur.fetchall()
    return [{"element_id": r[0], "name": r[1]} for r in rows]


@app.get("/archive/subsystems")
async def subsystems():
    async with pool.connection() as conn:
        cur = conn.cursor()
        await cur.execute("SELECT system_id, subsystem FROM subsystems ORDER BY system_id")
        rows = await cur.fetchall()
    return [{"system_id": r[0], "subsystem": r[1]} for r in rows]


@app.get("/archive/extent")
async def extent():
    async with pool.connection() as conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT extract(epoch FROM min(ts))::float8, "
            "       extract(epoch FROM max(ts))::float8 "
            "FROM eventhistory")
        lo, hi = await cur.fetchone()
    return {"t_min": lo, "t_max": hi}


@app.get("/archive/eventhistory")
async def eventhistory(
    element_id: int, t0: str, t1: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=PAGE_LIMIT, ge=1, le=PAGE_LIMIT),
):
    lo_s, hi_s = parse_ts(t0), parse_ts(t1)
    if hi_s <= lo_s:
        raise HTTPException(400, "t1 must be after t0")

    async with pool.connection() as conn:
        cur = conn.cursor()
        await cur.execute("SELECT 1 FROM hardware_mapping WHERE element_id=%s", (element_id,))
        if not await cur.fetchone():
            raise HTTPException(404, f"unknown element_id {element_id}")

        await cur.execute(
            "SELECT extract(epoch FROM ts)::float8, value, quality_flag "
            "FROM eventhistory "
            "WHERE element_id=%s AND ts>=to_timestamp(%s) AND ts<to_timestamp(%s) "
            "ORDER BY ts OFFSET %s LIMIT %s",
            (element_id, lo_s, hi_s, offset, limit + 1))
        rows = await cur.fetchall()
        more = len(rows) > limit
        if more:
            rows = rows[:limit]

        return {
            "element_id": element_id,
            "rows": len(rows),
            "next_offset": offset + len(rows) if more else None,
            "t": [round(r[0], 3) for r in rows],
            "value": [r[1] for r in rows],
            "quality_flag": [r[2] for r in rows],
        }


@app.get("/archive/eventhistory/count")
async def eventhistory_count(element_id: int, t0: str, t1: str):
    lo_s, hi_s = parse_ts(t0), parse_ts(t1)
    async with pool.connection() as conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT count(*) FROM eventhistory "
            "WHERE element_id=%s AND ts>=to_timestamp(%s) AND ts<to_timestamp(%s)",
            (element_id, lo_s, hi_s))
        cnt = (await cur.fetchone())[0]
        return {"element_id": element_id, "count": cnt}


@app.get("/archive/eventhistory.csv")
async def eventhistory_csv(element_id: int, t0: str, t1: str):
    lo_s, hi_s = parse_ts(t0), parse_ts(t1)
    if hi_s <= lo_s:
        raise HTTPException(400, "t1 must be after t0")

    async def gen():
        yield b"t,value,quality_flag\n"
        async with pool.connection() as conn:
            cur = conn.cursor()
            await cur.execute("SELECT 1 FROM hardware_mapping WHERE element_id=%s", (element_id,))
            if not await cur.fetchone():
                return
            await cur.execute(
                "SELECT extract(epoch FROM ts)::float8, value, quality_flag "
                "FROM eventhistory "
                "WHERE element_id=%s AND ts>=to_timestamp(%s) AND ts<to_timestamp(%s) "
                "ORDER BY ts",
                (element_id, lo_s, hi_s))
            async for r in cur:
                yield f"{r[0]:.3f},{r[1]:.6g},{r[2]}\n".encode()

    return StreamingResponse(gen(), media_type="text/csv")


@app.get("/archive/transitions")
async def transitions(t0: str, t1: str, system_id: int | None = None):
    lo, hi = parse_ts(t0), parse_ts(t1)
    async with pool.connection() as conn:
        cur = conn.cursor()
        q = ("SELECT transition_id, system_id, old_state_int, new_state_int, ts, operator_id "
             "FROM transitions WHERE ts >= %s AND ts < %s")
        params = [lo, hi]
        if system_id is not None:
            q += " AND system_id=%s"
            params.append(system_id)
        q += " ORDER BY ts"
        await cur.execute(q, params)
        rows = await cur.fetchall()
        return [{"transition_id": r[0], "system_id": r[1],
                 "old": r[2], "new": r[3], "ts": r[4],
                 "operator_id": r[5]} for r in rows]
