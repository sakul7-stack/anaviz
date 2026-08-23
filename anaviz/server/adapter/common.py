"""Pure helpers: timestamps, gap detection, coverage, heatmap math."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any
import numpy as np

_tasks: set[asyncio.Task] = set()


def spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain_background() -> None:
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)


def parse_timestamp(value: str | float) -> datetime:
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


def find_gap_intervals(timestamps: np.ndarray, bucket_seconds: float | int | None
                       ) -> list[list[float]]:
    if len(timestamps) < 2:
        return []
    deltas = np.diff(np.asarray(timestamps, dtype=float))
    positive = deltas[deltas > 0]
    if len(positive) == 0:
        return []
    expected = float(bucket_seconds) if bucket_seconds else float(np.median(positive))
    threshold = expected * (2.5 if bucket_seconds else 1.5)
    return [[float(s), float(e)]
            for s, e, d in zip(timestamps[:-1], timestamps[1:], deltas) if d > threshold]


def coverage_gaps(start: datetime, end: datetime,
                  intervals: list[tuple[datetime, datetime]]
                  ) -> list[tuple[datetime, datetime]]:
    clipped = sorted((max(start, a), min(end, b))
                     for a, b in intervals if b > start and a < end)
    gaps, cursor = [], start
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


def subtract_probed(a: datetime, b: datetime,
                    probed: list[tuple[datetime, datetime]]
                    ) -> list[tuple[datetime, datetime]]:
    pts = sorted(set([a, b] + [
        p for x, y in probed if y > a and x < b for p in (x, y) if a <= p <= b]))
    return [(lo, hi) for lo, hi in zip(pts, pts[1:])
            if not any(x <= lo and hi <= y for x, y in probed)]


def bin_values(t: np.ndarray, v: np.ndarray, edges: np.ndarray) -> np.ndarray:
    n = len(edges) - 1
    result = np.full(n, np.nan)
    if len(t) == 0:
        return result
    idx = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, n - 1)
    for b in range(n):
        mask = idx == b
        if mask.any():
            result[b] = float(np.nanmean(v[mask]))
    return result


def rolling_zscore(values: np.ndarray, window: int) -> np.ndarray:
    z = np.full(len(values), np.nan)
    observed: list[float] = []
    for i, value in enumerate(values):
        if not np.isfinite(value):
            continue
        baseline = np.asarray(observed[-window:], dtype=float)
        if len(baseline) >= 2:
            mu, sd = float(np.mean(baseline)), float(np.std(baseline, ddof=1))
            z[i] = 0.0 if sd < 1e-9 else (float(value) - mu) / sd
        observed.append(float(value))
    return z


def coerce_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if np.isfinite(n) else None


def coerce_quality(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
