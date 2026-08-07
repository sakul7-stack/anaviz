"""Pixel-budget downsampling: dispatch + band assembly."""
from __future__ import annotations

import time

import numpy as np

from .lttb import lttb_indices, lttb_segments
from .m4 import m4_series
from .minmax_lttb import minmax_lttb_indices


def downsample(payload: dict, algo: str, budget: int) -> tuple[dict, float]:
    """Apply downsampling to fit the pixel budget. Returns (payload, elapsed_ms)."""
    n = len(payload["t"])
    if n == 0 or algo == "RAW":
        return payload, 0.0

    t0 = time.perf_counter()
    if algo == "M4":
        if "min" in payload:
            idx, ts, vals = m4_series(
                payload["t"], payload["avg"], budget,
                payload["min"], payload["max"],
                payload["first"], payload["last"],
                payload["min_t"], payload["max_t"],
                payload["first_t"], payload["last_t"])
        else:
            idx, ts, vals = m4_series(payload["t"], payload["avg"], budget)
        result = {key: arr[idx] for key, arr in payload.items()}
        result["t"], result["avg"] = ts, vals

    elif algo == "MINMAXLTTB":
        if n <= budget:
            return payload, 0.0
        idx, counts, starts = minmax_lttb_indices(payload["t"], payload["avg"], budget)
        result = {key: arr[idx] for key, arr in payload.items()}
        if "min" in payload:
            result = _apply_band(result, payload, starts, counts)
    else:  # LTTB
        if n <= budget:
            return payload, 0.0
        idx = lttb_indices(payload["t"], payload["avg"], budget)
        result = {key: arr[idx] for key, arr in payload.items()}
        if "min" in payload:
            starts, _ = lttb_segments(n, budget)
            result = _apply_band(result, payload, starts)

    return result, (time.perf_counter() - t0) * 1000


def _apply_band(result: dict, payload: dict, starts: np.ndarray,
                counts: np.ndarray | None = None) -> dict:
    if counts is None:
        result["min"] = np.minimum.reduceat(payload["min"], starts)
        result["max"] = np.maximum.reduceat(payload["max"], starts)
    else:
        result["min"] = np.repeat(np.minimum.reduceat(payload["min"], starts), counts)
        result["max"] = np.repeat(np.maximum.reduceat(payload["max"], starts), counts)
    result["min_t"] = result["t"]
    result["max_t"] = result["t"]
    return result
