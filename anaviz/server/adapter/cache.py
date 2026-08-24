"""Generic fixed-schema Postgres cache.

Tables: cache_series, cache_coverage, cache_rollup.
Rows are isolated by dataset_key so multiple datasets never collide.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from psycopg_pool import AsyncConnectionPool

from .downsample.rollup import aggregate_buckets
from .sql import (
    SCHEMA_DDL, COVERAGE, STORE_COVERAGE, STORE_SERIES,
    COUNT, READ, READ_BUCKETED, UPSERT_ROLLUP, READ_ROLLUP,
    DROP_SERIES, DROP_COVERAGE, DROP_ROLLUP, CLEAR_ALL,
)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


@dataclass
class RollupData:
    """Bucketed data for a rollup tier."""
    t: np.ndarray; avg: np.ndarray
    first: np.ndarray; first_t: np.ndarray
    min: np.ndarray; min_t: np.ndarray
    max: np.ndarray; max_t: np.ndarray
    last: np.ndarray; last_t: np.ndarray
    n: np.ndarray; q: np.ndarray
    total: int; truncated: bool

    @staticmethod
    def empty() -> "RollupData":
        e = np.array([], dtype=float)
        return RollupData(t=e, avg=e, first=e, first_t=e, min=e, min_t=e,
                          max=e, max_t=e, last=e, last_t=e,
                          n=np.array([], np.int64), q=np.array([], np.int16),
                          total=0, truncated=False)

    @staticmethod
    def from_rows(rows: list[tuple], *, truncated=False) -> "RollupData":
        if not rows:
            return RollupData.empty()
        col = lambda i, dt=float: np.array([r[i] for r in rows], dtype=dt)
        return RollupData(
            t=col(2), avg=col(9),
            first=col(1), first_t=col(2), min=col(3), min_t=col(4),
            max=col(5), max_t=col(6), last=col(7), last_t=col(8),
            n=col(10, np.int64), q=col(11, np.int16),
            total=int(sum(r[10] for r in rows)), truncated=truncated)


class GenericCache:
    """Fixed-schema Postgres cache namespaced per dataset_key."""

    def __init__(self, pool: AsyncConnectionPool, dataset_key: str):
        self.pool = pool
        self.dataset_key = dataset_key

    # Schema
    @staticmethod
    async def ensure_tables(cur) -> None:
        for stmt in SCHEMA_DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                await cur.execute(stmt)

    # Coverage
    async def coverage(self, entity_id, measure_id, start, end):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                COVERAGE, (self.dataset_key, entity_id, measure_id, start, end))
            return [(a, b) for a, b in await cur.fetchall()]

    # Store
    async def store(self, entity_id, measure_id, rows, intervals, *,
                    rollup_levels=None):
        if not rows and not intervals:
            return
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cur = conn.cursor()
                if rows:
                    buf = io.StringIO()
                    for ts, value, quality in rows:
                        buf.write(f"{ts.isoformat()},{value},{quality}\n")
                    buf.seek(0)
                    await cur.execute(
                        "CREATE TEMP TABLE _cs (ts TIMESTAMPTZ, "
                        "value DOUBLE PRECISION, quality SMALLINT) ON COMMIT DROP")
                    async with cur.copy("COPY _cs FROM STDIN WITH (FORMAT csv)") as ctx:
                        while chunk := buf.read(1 << 20):
                            await ctx.write(chunk.encode())
                    await cur.execute(
                        STORE_SERIES, (self.dataset_key, entity_id, measure_id))
                    if rollup_levels:
                        t_arr = np.array([r[0].timestamp() for r in rows], dtype=float)
                        v_arr = np.array([r[1] for r in rows], dtype=float)
                        q_arr = np.array([r[2] for r in rows], dtype=np.int16)
                        buckets = aggregate_buckets(t_arr, v_arr, q_arr, rollup_levels)
                        if buckets:
                            await self._store_rollup(cur, entity_id, measure_id, buckets)
                if intervals:
                    for a, b, status in intervals:
                        await cur.execute(
                            STORE_COVERAGE,
                            (self.dataset_key, entity_id, measure_id, a, b, status))

    async def _store_rollup(self, cur, entity_id, measure_id, bucket_rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        for bkt_s, rows in bucket_rows.items():
            for row in rows:
                w.writerow([
                    entity_id, measure_id, bkt_s,
                    _iso(row[0]), row[1], _iso(row[2]),
                    row[3], _iso(row[4]), row[5], _iso(row[6]),
                    row[7], _iso(row[8]), row[9], int(row[10]), int(row[11]),
                ])
        buf.seek(0)
        await cur.execute(
            "CREATE TEMP TABLE _rs ("
            "entity_id TEXT, measure_id TEXT, bucket_s DOUBLE PRECISION, "
            "bucket_start TIMESTAMPTZ, v_first DOUBLE PRECISION, "
            "first_ts TIMESTAMPTZ, v_min DOUBLE PRECISION, min_ts TIMESTAMPTZ, "
            "v_max DOUBLE PRECISION, max_ts TIMESTAMPTZ, v_last DOUBLE PRECISION, "
            "last_ts TIMESTAMPTZ, v_avg DOUBLE PRECISION, n INTEGER, "
            "q_worst SMALLINT) ON COMMIT DROP")
        async with cur.copy("COPY _rs FROM STDIN WITH (FORMAT csv)") as ctx:
            while chunk := buf.read(1 << 20):
                await ctx.write(chunk.encode())
        await cur.execute(UPSERT_ROLLUP, (self.dataset_key,))

    async def store_rollup(self, entity_id, measure_id, bucket_rows):
        if not bucket_rows:
            return
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await self._store_rollup(conn.cursor(), entity_id, measure_id, bucket_rows)

    # Read
    async def read_rollup(self, entity_id, measure_id, bucket_s, start, end, cap):
        lo = datetime.fromtimestamp(
            (start.timestamp() // bucket_s) * bucket_s, tz=timezone.utc)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                READ_ROLLUP, (self.dataset_key, entity_id, measure_id,
                              bucket_s, lo, end, cap))
            return RollupData.from_rows(await cur.fetchall())

    async def count(self, entity_id, measure_id, start, end):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                COUNT, (self.dataset_key, entity_id, measure_id, start, end))
            return int((await cur.fetchone())[0])

    async def read_bucketed(self, entity_id, measure_id, bucket_s, start, end, cap):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                READ_BUCKETED, (bucket_s, bucket_s, self.dataset_key,
                                entity_id, measure_id, start, end, cap))
            rows = await cur.fetchall()
        return RollupData.from_rows(rows, truncated=len(rows) >= cap)

    async def read(self, entity_id, measure_id, start, end, cap):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                READ, (self.dataset_key, entity_id, measure_id, start, end, cap))
            rows = await cur.fetchall()
        if not rows:
            return (np.array([], dtype=float), np.array([], dtype=float),
                    np.array([], np.int16), 0, False)
        total = int(rows[0][3])
        t = np.array([r[0] for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
        q = np.array([r[2] for r in rows], dtype=np.int16)
        return t, v, q, total, len(rows) < total

    # Delete
    async def drop_dataset(self):
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cur = conn.cursor()
                for sql in (DROP_SERIES, DROP_COVERAGE, DROP_ROLLUP):
                    await cur.execute(sql, (self.dataset_key,))

    @staticmethod
    async def clear_all(cur):
        await cur.execute(CLEAR_ALL)
