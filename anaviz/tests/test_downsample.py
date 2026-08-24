import numpy as np
from server.adapter.downsample import downsample
from server.adapter.downsample.lttb import lttb_indices
from server.adapter.downsample.m4 import m4_series


def test_lttb_keeps_spike():
    rng = np.random.default_rng(1)
    n = 6000
    t = np.arange(n, dtype=float)
    v = np.sin(t / 200) + rng.normal(0, 0.05, n)
    v[3000] += 5.0

    idx = lttb_indices(t, v, 120)
    assert len(idx) == 120
    assert idx[0] == 0 and idx[-1] == n - 1
    assert (np.diff(idx) > 0).all()
    assert 3000 in idx
    uni = np.linspace(0, n - 1, 120).astype(int)
    assert 3000 not in uni


def test_lttb_edge_cases():
    t = np.arange(10, dtype=float)
    v = t * 2
    assert len(lttb_indices(t, v, 500)) == 10
    assert len(lttb_indices(t, v, 2)) == 10


def test_lttb_band_envelopes_line():
    """    LTTB over budget: the band is the true per-segment min/max at the
    SAME timestamps as the line, so the avg line never pokes out of it."""
    rng = np.random.default_rng(2)
    n = 2000
    t = np.arange(n, dtype=float)
    v_avg = np.sin(t / 100) + rng.normal(0, 0.05, n)
    v_min = v_avg - rng.uniform(0.1, 1.0, n)
    v_max = v_avg + rng.uniform(0.1, 1.0, n)
    payload = {
        "t": t, "avg": v_avg,
        "min": v_min, "max": v_max,
        "min_t": t + 0.2, "max_t": t + 0.4,
        "first": v_avg, "last": v_avg,
        "first_t": t + 0.1, "last_t": t + 0.3,
    }
    result, _ = downsample(payload, "LTTB", 100)
    assert len(result["t"]) == 100
    assert np.array_equal(result["min_t"], result["t"])
    assert np.array_equal(result["max_t"], result["t"])
    assert (result["min"] < result["avg"]).all()  # nodes bound the line,
    assert (result["avg"] < result["max"]).all()  # linearity extends it between nodes


def test_lttb_within_budget_keeps_full_arrays():
    """LTTB within budget returns the payload untouched (band = stored min/max)."""
    n = 50
    t = np.arange(n, dtype=float)
    payload = {
        "t": t, "avg": np.sin(t / 10),
        "min": -np.ones(n), "max": np.ones(n),
        "min_t": t + 0.2, "max_t": t + 0.4,
        "first": t, "last": t,
        "first_t": t + 0.1, "last_t": t + 0.3,
    }
    result, _ = downsample(payload, "LTTB", 500)
    assert len(result["t"]) == n
    assert np.array_equal(result["min_t"], t + 0.2)


def test_m4_basic():
    rng = np.random.default_rng(1)
    n = 200
    t = np.arange(n, dtype=float)
    v = np.sin(t / 20) + rng.normal(0, 0.05, n)

    idx, ts, vals = m4_series(t, v, 40)
    assert len(idx) <= 40
    assert len(idx) >= 10
    assert (np.diff(idx) > 0).all()
    assert np.array_equal(ts, t[idx])
    assert np.array_equal(vals, v[idx])


def test_m4_expands_all_buckets():
    """n <= budget with enriched rollup arrays: every bucket contributes its
    first/min/max/last extreme (4 points, sorted by time), not a single avg."""
    n = 6
    t = np.arange(n, dtype=float)
    v_avg = np.zeros(n)
    v_first = np.arange(n, dtype=float)
    v_last = v_first + 100
    v_min = v_first + 10
    v_max = v_first + 50
    first_t = t + 0.1
    min_t = t + 0.2
    max_t = t + 0.3
    last_t = t + 0.4

    idx, ts, vals = m4_series(t, v_avg, 100, v_min, v_max, v_first, v_last,
                              min_t, max_t, first_t, last_t)
    assert len(ts) == 4 * n
    assert (np.diff(ts) > 0).all()
    assert ts[0] == first_t[0] and vals[0] == v_first[0]
    assert ts[-1] == last_t[-1] and vals[-1] == v_last[-1]
    assert set(ts) == set(first_t) | set(min_t) | set(max_t) | set(last_t)
    assert set(vals) == set(v_first) | set(v_min) | set(v_max) | set(v_last)


def test_m4_uses_extreme_arrays():
    """With enriched arrays, M4 picks the bucket with the true extreme and
    returns its exact timestamp + value (not the bucket epoch + avg)."""
    n = 24
    t = np.arange(n, dtype=float)
    v_avg = np.full(n, 0.5)          # all averages are boring
    v_min = np.full(n, -1.0)
    v_max = np.full(n, 1.0)
    v_first = np.full(n, 0.0)
    v_last = np.full(n, 0.0)
    v_min[10] = -100.0                # extreme min at idx 10
    v_max[15] = 100.0                 # extreme max at idx 15
    min_t = t + 0.1
    max_t = t + 0.2
    first_t = t + 0.3
    last_t = t + 0.4

    idx, ts, vals = m4_series(t, v_avg, 8, v_min, v_max, v_first, v_last,
                              min_t, max_t, first_t, last_t)
    assert 10 in idx, "must pick the bucket with the true minimum"
    assert 15 in idx, "must pick the bucket with the true maximum"

    pos_min = np.where(idx == 10)[0][0]
    assert ts[pos_min] == min_t[10], "min point must use the exact min timestamp"
    assert vals[pos_min] == v_min[10], "min point must use the extreme value"

    pos_max = np.where(idx == 15)[0][0]
    assert ts[pos_max] == max_t[15], "max point must use the exact max timestamp"
    assert vals[pos_max] == v_max[15], "max point must use the extreme value"


def test_minmax_lttb_keeps_spike_and_dip():
    from server.adapter.downsample.minmax_lttb import minmax_lttb_indices
    rng = np.random.default_rng(3)
    n = 6000
    t = np.arange(n, dtype=float)
    v = np.sin(t / 200) + rng.normal(0, 0.05, n)
    v[1500] -= 5.0
    v[4500] += 5.0

    idx, counts, starts = minmax_lttb_indices(t, v, 120)
    assert len(idx) == 120
    assert idx[0] == 0 and idx[-1] == n - 1
    assert (np.diff(idx) > 0).all()
    assert 1500 in idx and 4500 in idx
    # starts/counts bookkeeping used by the band code in app.py
    assert len(starts) == len(counts)
    assert starts[0] == 0
    assert (np.diff(starts) > 0).all()
    assert int(counts.sum()) == len(idx)


def test_minmax_lttb_band_envelopes_line():
    from server.adapter.downsample.minmax_lttb import minmax_lttb_indices
    rng = np.random.default_rng(4)
    n = 2000
    t = np.arange(n, dtype=float)
    v_avg = np.sin(t / 100) + rng.normal(0, 0.05, n)
    v_min = v_avg - rng.uniform(0.1, 1.0, n)
    v_max = v_avg + rng.uniform(0.1, 1.0, n)
    payload = {
        "t": t, "avg": v_avg,
        "min": v_min, "max": v_max,
        "min_t": t + 0.2, "max_t": t + 0.4,
        "first": v_avg, "last": v_avg,
        "first_t": t + 0.1, "last_t": t + 0.3,
    }
    result, _ = downsample(payload, "MINMAXLTTB", 100)
    assert len(result["t"]) == 100
    assert np.array_equal(result["min_t"], result["t"])
    assert (result["min"] < result["avg"]).all()  # nodes bound the line,
    assert (result["avg"] < result["max"]).all()  # linearity extends it between nodes

    # Stronger check than the inequalities above: those would still pass
    # even if the band came from misaligned buckets, since v_min/v_max are
    # pointwise offsets of v_avg at every original row. Cross-check the
    # actual values against the picker's own buckets directly.
    _, counts, starts = minmax_lttb_indices(t, v_avg, 100)
    expected_min = np.repeat(np.minimum.reduceat(v_min, starts), counts)
    expected_max = np.repeat(np.maximum.reduceat(v_max, starts), counts)
    assert np.array_equal(result["min"], expected_min)
    assert np.array_equal(result["max"], expected_max)
