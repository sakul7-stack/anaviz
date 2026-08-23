"""Largest-Triangle-Three-Buckets (LTTB) downsampling."""
from __future__ import annotations
import numpy as np


def lttb_indices(t: np.ndarray, v: np.ndarray, n_out: int) -> np.ndarray:
    n = len(t)
    if n_out >= n or n_out < 3:
        return np.arange(n)
    bounds = np.linspace(1, n - 1, n_out - 1).astype(np.int64)
    bounds = np.maximum(bounds, np.arange(len(bounds)) + 1)
    seg = np.append(bounds, n)
    counts = np.diff(seg).astype(float)
    avg_t = np.add.reduceat(t, seg[:-1]) / counts
    avg_v = np.add.reduceat(v, seg[:-1]) / counts
    out = np.empty(n_out, dtype=np.int64)
    out[0] = 0; out[-1] = n - 1
    anchor = 0
    for i in range(n_out - 2):
        lo, hi = bounds[i], bounds[i + 1]
        ta, va = t[anchor], v[anchor]
        area = np.abs((ta - avg_t[i + 1]) * (v[lo:hi] - va) - (ta - t[lo:hi]) * (avg_v[i + 1] - va))
        anchor = lo + int(area.argmax())
        out[i + 1] = anchor
    return out


def lttb_segments(n: int, n_out: int) -> tuple[np.ndarray, np.ndarray]:
    bounds = np.linspace(1, n - 1, n_out - 1).astype(np.int64)
    bounds = np.maximum(bounds, np.arange(len(bounds)) + 1)
    return np.r_[0, bounds], np.r_[bounds, n]
