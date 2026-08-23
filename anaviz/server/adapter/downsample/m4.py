"""M4 downsampling: per sub-group first/min/max/last with exact timestamps."""
from __future__ import annotations
import numpy as np


def m4_series(t, avg, n_out, v_min=None, v_max=None, v_first=None, v_last=None,
              min_t=None, max_t=None, first_t=None, last_t=None):
    n = len(t)
    if n_out < 4:
        return np.arange(n), t, avg
    enriched = all(x is not None for x in (v_min, v_max, min_t, max_t, v_first, v_last, first_t, last_t))
    if n_out >= n:
        if not enriched:
            return np.arange(n), t, avg
        idxs = np.repeat(np.arange(n), 4)
        ts = np.stack([first_t, min_t, max_t, last_t], axis=1).ravel()
        vals = np.stack([v_first, v_min, v_max, v_last], axis=1).ravel()
        order = np.argsort(ts, kind="stable")
        return idxs[order], ts[order], vals[order]
    n_buckets = n_out // 4
    bounds = np.linspace(0, n, n_buckets + 1).astype(np.int64)
    idxs, ts, vals = [], [], []
    for i in range(n_buckets):
        lo, hi = bounds[i], bounds[i + 1]
        if hi - lo < 2:
            idxs.append(lo); ts.append(t[lo]); vals.append(avg[lo]); continue
        i_first = lo
        if enriched:
            i_min = lo + int(np.argmin(v_min[lo:hi]))
            i_max = lo + int(np.argmax(v_max[lo:hi]))
        else:
            seg = avg[lo:hi]
            i_min = lo + int(seg.argmin()); i_max = lo + int(seg.argmax())
        i_last = hi - 1
        seen = set()
        for idx in (i_first, i_min, i_max, i_last):
            if idx in seen: continue
            seen.add(idx); idxs.append(idx)
            if enriched and idx == i_min: ts.append(min_t[idx]); vals.append(v_min[idx])
            elif enriched and idx == i_max: ts.append(max_t[idx]); vals.append(v_max[idx])
            elif enriched and idx == i_first: ts.append(first_t[idx]); vals.append(v_first[idx])
            elif enriched and idx == i_last: ts.append(last_t[idx]); vals.append(v_last[idx])
            else: ts.append(t[idx]); vals.append(avg[idx])
    idxs = np.array(idxs, dtype=np.int64)
    order = np.argsort(idxs)
    return idxs[order], np.array(ts)[order], np.array(vals)[order]
