"""Opt-in rollup tiers for generic datasets.

The project cache stores raw samples; large ranges can be served from a generic
bucket rollup (first/min/max/last/avg/count per time bucket) built lazily from
raw rows already in the cache. Tiers are strictly opt-in: a ``config.json``
opts in by declaring explicit ``rollup_levels`` bucket sizes (seconds);
``rollup_enabled`` defaults to true and can be set false as a kill-switch.
Nothing is derived automatically from the source's sampling step — no
continuous aggregates, no TimescaleDB, no per-source tables.

Bucket grid is anchored at the Unix epoch so independent hydration ranges
merge cleanly in ``cache_rollup`` (PK includes ``bucket_start``).
"""
from __future__ import annotations

import numpy as np

TIER_MULTIPLIER = 4      # a bucket must beat ~4x the finest tier to be useful


def select_tier(
    span_s: float,
    pixel_width: int,
    points_per_pixel: int,
    levels: list[float] | None = None,
) -> str | float:
    """Pick ``"raw"`` or a bucket size (seconds) for a query.

    Coarsest tier with ``bucket <= span / (px * k)`` (at least ``k`` buckets per
    pixel). When no level can reduce the row count (target smaller than 4x the
    finest tier) or no explicit levels are declared, ``"raw"`` wins.
    """
    if span_s <= 0 or not levels:
        return "raw"
    levels = sorted(levels)
    target = span_s / max(pixel_width * points_per_pixel, 1)
    if target < levels[0] * TIER_MULTIPLIER:
        return "raw"
    chosen = levels[0]
    for level in levels:
        if level <= target:
            chosen = level
    return chosen


def tier_name(bucket_s: float) -> str:
    """Human label for a bucket size: ``"1s"``, ``"5m"``, ``"1h8m16s"``, ..."""
    s = int(round(bucket_s))
    if s < 60:
        return f"{s}s"
    parts = []
    if s >= 86400:
        parts.append(f"{s // 86400}d")
        s %= 86400
    if s >= 3600:
        parts.append(f"{s // 3600}h")
        s %= 3600
    if s >= 60:
        parts.append(f"{s // 60}m")
        s %= 60
    if s:
        parts.append(f"{s}s")
    return "".join(parts)


def aggregate_buckets(
    t: np.ndarray, v: np.ndarray, q: np.ndarray, levels: list[float],
) -> dict[float, list[tuple]]:
    """Aggregate sorted raw samples into per-level bucket rows for the cache.

    Returns ``{bucket_s: [bucket tuples]}`` ordered by bucket start. Min ties
    keep the earliest timestamp, max ties the latest (matching the old
    ``first(ts, value)`` / ``last(ts, value)`` rollup semantics).
    """
    if len(t) == 0 or not levels:
        return {}
    result: dict[float, list[tuple]] = {}
    for bucket_s in levels:
        idx = (t // bucket_s).astype(np.int64)
        order = np.argsort(idx, kind="stable")
        oidx = idx[order]
        ot = t[order]
        ov = v[order]
        oq = q[order]
        starts = np.flatnonzero(np.concatenate(
            ([True], oidx[1:] != oidx[:-1])))
        ends = np.concatenate((starts[1:], [len(ot)]))
        counts = ends - starts
        group = np.searchsorted(starts, np.arange(len(ot)),
                                side="right") - 1
        vmin = np.minimum.reduceat(ov, starts)
        vmax = np.maximum.reduceat(ov, starts)
        min_ts = np.minimum.reduceat(
            np.where(ov == vmin[group], ot, np.inf), starts)
        max_ts = np.maximum.reduceat(
            np.where(ov == vmax[group], ot, -np.inf), starts)
        v_avg = np.add.reduceat(ov, starts) / counts
        q_worst = np.maximum.reduceat(oq, starts)

        rows: list[tuple] = []
        for k in range(len(starts)):
            rows.append((
                float(oidx[starts[k]] * bucket_s),
                float(ov[starts[k]]), float(ot[starts[k]]),
                float(vmin[k]), float(min_ts[k]),
                float(vmax[k]), float(max_ts[k]),
                float(ov[ends[k] - 1]), float(ot[ends[k] - 1]),
                float(v_avg[k]), int(counts[k]), int(q_worst[k]),
            ))
        result[bucket_s] = rows
    return result
