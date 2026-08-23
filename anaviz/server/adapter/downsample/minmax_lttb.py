"""MinMaxLTTB: two picks per bucket (peak + valley)."""
from __future__ import annotations
import numpy as np


def minmax_lttb_indices(t, v, n_out):
    n = len(t)
    if n_out >= n or n_out < 4:
        return np.arange(n), np.ones(n, dtype=np.int64), np.arange(n)
    n_buckets = n_out // 2 + 1
    bounds = np.linspace(1, n - 1, n_buckets - 1).astype(np.int64)
    bounds = np.maximum(bounds, np.arange(len(bounds)) + 1)
    seg = np.append(bounds, n)
    counts_f = np.diff(seg).astype(float)
    avg_t = np.add.reduceat(t, seg[:-1]) / counts_f
    avg_v = np.add.reduceat(v, seg[:-1]) / counts_f
    out, per_bucket = [0], [1]
    anchor = 0
    for i in range(n_buckets - 2):
        lo, hi = bounds[i], bounds[i + 1]
        if hi - lo == 1:
            out.append(lo); per_bucket.append(1); anchor = lo; continue
        t0, v0 = t[anchor], v[anchor]
        area = (t0 - avg_t[i + 1]) * (v[lo:hi] - v0) - (t0 - t[lo:hi]) * (avg_v[i + 1] - v0)
        i_peak = lo + int(area.argmax())
        area[i_peak - lo] = np.inf
        i_val = lo + int(area.argmin())
        a, b = (i_peak, i_val) if i_peak < i_val else (i_val, i_peak)
        out.extend((a, b)); per_bucket.append(2); anchor = b
    out.append(n - 1); per_bucket.append(1)
    return (np.array(out, dtype=np.int64), np.array(per_bucket, dtype=np.int64),
            np.concatenate(([0], bounds)).astype(np.int64))
