"""Pure helpers shared by config-driven adapters.

These functions were extracted from the HLT-specific cache/service/heatmap
modules so the generic adapter can reuse the same gap, coverage, downsampling,
and matrix math without any dependency on a particular database schema.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import numpy as np

# ── Background task tracking ──────────────────────────────────────────────────

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn_background(coro) -> asyncio.Task:
    """Track a fire-and-forget coroutine so tests/shutdown can drain it."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def drain_background() -> None:
    if _BACKGROUND_TASKS:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)


# ── Timestamps ────────────────────────────────────────────────────────────────

def parse_timestamp(value: str | float) -> datetime:
    """Parse a Unix timestamp or ISO-8601 value as an aware UTC datetime."""
    text = str(value)
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"bad timestamp: {text!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── Gap detection ─────────────────────────────────────────────────────────────

def find_gap_intervals(
    timestamps: np.ndarray, bucket_seconds: float | int | None
) -> list[list[float]]:
    """Find source gaps before downsampling, never from display-selected nodes.

    ``bucket_seconds`` is the expected sampling interval when known (from a
    rollup or a configured ``expected_step_seconds``); when 0/None the median
    observed delta is used. For bucketed data, one or two missing consecutive
    buckets can occur naturally at sparse run boundaries; only declare a gap
    when more than two consecutive expected intervals are missing. For raw
    data a single skipped median interval is enough to indicate a gap.
    """
    if len(timestamps) < 2:
        return []
    deltas = np.diff(np.asarray(timestamps, dtype=float))
    positive = deltas[deltas > 0]
    if len(positive) == 0:
        return []
    expected = (float(bucket_seconds) if bucket_seconds
                else float(np.median(positive)))
    threshold = expected * (2.5 if bucket_seconds else 1.5)
    return [[float(start), float(end)]
            for start, end, delta in zip(
                timestamps[:-1], timestamps[1:], deltas)
            if delta > threshold]


# ── Coverage interval arithmetic ──────────────────────────────────────────────

def coverage_gaps(
    start: datetime,
    end: datetime,
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Return uncovered half-open portions of [start, end)."""
    clipped = sorted((max(start, a), min(end, b))
                     for a, b in intervals if b > start and a < end)
    gaps: list[tuple[datetime, datetime]] = []
    cursor = start
    for a, b in clipped:
        if b <= cursor:
            continue
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return [(a, b) for a, b in gaps if b > a]


def subtract_probed(
    a: datetime, b: datetime, probed: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """Split [a, b) around intervals already known to contain no samples."""
    pts = sorted(set([a, b] + [
        p for x, y in probed if y > a and x < b for p in (x, y) if a <= p <= b]))
    gaps = []
    for lo, hi in zip(pts, pts[1:]):
        if not any(x <= lo and hi <= y for x, y in probed):
            gaps.append((lo, hi))
    return gaps


# ── Heatmap math (per-entity temporal z-score) ────────────────────────────────

def bin_values(t_arr: np.ndarray, v_arr: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Average values into time buckets; empty buckets stay NaN (missing)."""
    n = len(edges) - 1
    result = np.full(n, np.nan)
    if len(t_arr) == 0:
        return result
    indices = np.clip(np.searchsorted(edges, t_arr, side="right") - 1, 0, n - 1)
    for b in range(n):
        mask = indices == b
        if mask.any():
            result[b] = float(np.nanmean(v_arr[mask]))
    return result


def rolling_zscore(values: np.ndarray, window: int) -> np.ndarray:
    """Rolling z-score against the trailing observed baseline (missing-aware)."""
    z = np.full(len(values), np.nan)
    observed: list[float] = []
    for i, value in enumerate(values):
        if not np.isfinite(value):
            continue
        baseline = np.asarray(observed[-window:], dtype=float)
        if len(baseline) >= 2:
            mu = float(np.mean(baseline))
            sd = float(np.std(baseline, ddof=1))
            z[i] = 0.0 if sd < 1e-9 else (float(value) - mu) / sd
        observed.append(float(value))
    return z


# ── Small utilities ───────────────────────────────────────────────────────────

def coerce_number(value: Any) -> float | None:
    """Coerce a raw upstream value to float, or None when it is missing/NaN."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def coerce_quality(value: Any) -> int:
    """Coerce an upstream quality flag to an integer (0 default)."""
    if value is None:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
