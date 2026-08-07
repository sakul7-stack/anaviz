"""
Heatmap grid builder — z-score anomaly heatmap across multiple channels.

For each channel, queries the appropriate rollup tier, divides the window
into `n_buckets` equal time buckets, computes a rolling z-score, clips to
[-3, 3] and normalises to [0, 1]. Returns a 2-D grid [n_channels][n_buckets].
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
from psycopg_pool import AsyncConnectionPool

from ..downsample.resolution import select_resolution

_ROLLING_WINDOW = 10


async def build_heatmap(
    pool: AsyncConnectionPool,
    channel_ids: list[int],
    start: datetime,
    end: datetime,
    n_buckets: int = 200,
) -> dict:
    """
    Returns::

        {
            "t":        [float, ...],        # unix epoch bucket centres
            "channels": [int, ...],
            "scores":   [[float, ...], ...], # [n_channels][n_buckets] in [0,1]
        }
    """
    if not channel_ids:
        return {"t": [], "channels": [], "scores": []}

    span = (end - start).total_seconds()
    if span <= 0:
        return {"t": [], "channels": channel_ids, "scores": [[] for _ in channel_ids]}

    t_start = start.timestamp()
    t_end = end.timestamp()
    bucket_edges = np.linspace(t_start, t_end, n_buckets + 1)
    bucket_centres = (bucket_edges[:-1] + bucket_edges[1:]) / 2.0

    resolution = select_resolution(span, n_buckets)

    async with pool.connection() as conn:
        if resolution.is_raw:
            rows_all = await _query_raw(conn, channel_ids, start, end)
        else:
            rows_all = await _query_rollup(conn, channel_ids, start, end, resolution)

    scores_grid: list[list[float]] = []
    for cid in channel_ids:
        raw = rows_all.get(cid, [])
        if not raw:
            scores_grid.append([0.0] * n_buckets)
            continue
        t_arr = np.array([r[0] for r in raw], dtype=float)
        v_arr = np.array([r[1] for r in raw], dtype=float)
        bucket_avg = _bin_values(t_arr, v_arr, bucket_edges)
        z = _rolling_zscore(bucket_avg, _ROLLING_WINDOW)
        normed = (np.clip(z, -3.0, 3.0) + 3.0) / 6.0
        scores_grid.append(np.round(normed, 4).tolist())

    return {
        "t": bucket_centres.tolist(),
        "channels": channel_ids,
        "scores": scores_grid,
    }


async def _query_raw(conn, channel_ids: list[int], start: datetime, end: datetime) -> dict:
    cur = await conn.execute(
        "SELECT element_id, extract(epoch FROM ts)::float8, value "
        "FROM eventhistory "
        "WHERE element_id = ANY(%s) AND ts >= %s AND ts < %s "
        "ORDER BY element_id, ts",
        (channel_ids, start, end),
    )
    result: dict[int, list] = {cid: [] for cid in channel_ids}
    for eid, ts, val in await cur.fetchall():
        result[eid].append((ts, val))
    return result


async def _query_rollup(conn, channel_ids: list[int], start: datetime,
                        end: datetime, resolution) -> dict:
    cur = await conn.execute(
        f"SELECT element_id, extract(epoch FROM bucket)::float8, v_avg "
        f"FROM {resolution.table} "
        f"WHERE element_id = ANY(%s) AND bucket >= %s AND bucket < %s "
        f"ORDER BY element_id, bucket",
        (channel_ids, start, end),
    )
    result: dict[int, list] = {cid: [] for cid in channel_ids}
    for eid, ts, val in await cur.fetchall():
        result[eid].append((ts, val))
    return result


def _bin_values(t_arr: np.ndarray, v_arr: np.ndarray, edges: np.ndarray) -> np.ndarray:
    n = len(edges) - 1
    result = np.full(n, np.nan)
    if len(t_arr) == 0:
        return result
    indices = np.clip(np.searchsorted(edges, t_arr, side="right") - 1, 0, n - 1)
    for b in range(n):
        mask = indices == b
        if mask.any():
            result[b] = float(np.nanmean(v_arr[mask]))
    # Forward-fill NaN gaps
    last_good = np.nan
    for i in range(n):
        if np.isnan(result[i]):
            result[i] = last_good
        else:
            last_good = result[i]
    return np.where(np.isnan(result), 0.0, result)


def _rolling_zscore(values: np.ndarray, window: int) -> np.ndarray:
    n = len(values)
    z = np.zeros(n)
    for i in range(n):
        baseline = values[max(0, i - window):i]
        if len(baseline) < 2:
            continue
        mu, sd = float(np.mean(baseline)), float(np.std(baseline, ddof=1))
        z[i] = 0.0 if sd < 1e-9 else (values[i] - mu) / sd
    return z
