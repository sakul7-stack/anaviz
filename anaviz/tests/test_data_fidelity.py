"""Data-fidelity tests: gaps, coverage, missingness, and quality coercion.

These exercise the pure helpers the configurable adapter reuses, so evidence
semantics (nulls and gaps are real, never filled) hold for arbitrary sources.
"""
from datetime import datetime, timedelta, timezone

import numpy as np

from server.adapter.common import (
    bin_values,
    coerce_number,
    coerce_quality,
    coverage_gaps,
    find_gap_intervals,
    rolling_zscore,
    subtract_probed,
)


def _dt(seconds: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def test_interval_coverage_preserves_internal_unqueried_gap():
    gaps = coverage_gaps(
        _dt(0), _dt(30),
        [(_dt(0), _dt(10)), (_dt(20), _dt(30))],
    )
    assert gaps == [(_dt(10), _dt(20))]


def test_interval_coverage_merges_overlapping_ranges_for_gap_math():
    gaps = coverage_gaps(
        _dt(0), _dt(30),
        [(_dt(0), _dt(12)), (_dt(10), _dt(25))],
    )
    assert gaps == [(_dt(25), _dt(30))]


def test_subtract_probed_removes_known_empty_ranges():
    gaps = subtract_probed(
        _dt(0), _dt(50),
        [(_dt(10), _dt(20)), (_dt(30), _dt(40))])
    assert gaps == [(_dt(0), _dt(10)), (_dt(20), _dt(30)), (_dt(40), _dt(50))]


def test_source_gap_metadata_is_calculated_before_downsampling():
    timestamps = np.array([0.0, 5.0, 10.0, 40.0, 45.0])
    assert find_gap_intervals(timestamps, None) == [[10.0, 40.0]]


def test_single_missing_expected_bucket_is_not_a_gap_but_large_skip_is():
    # Single missing bucket (120→240, delta=120, threshold=150) — NOT a gap
    timestamps = np.array([0.0, 60.0, 120.0, 240.0])
    assert find_gap_intervals(timestamps, 60) == []

    # Three missing buckets (60→300, delta=240, threshold=150) — IS a gap
    timestamps = np.array([0.0, 60.0, 300.0, 360.0])
    assert find_gap_intervals(timestamps, 60) == [[60.0, 300.0]]


def test_coerce_number_preserves_missing():
    assert coerce_number(None) is None
    assert coerce_number("nope") is None
    assert coerce_number(float("nan")) is None
    assert coerce_number("2.5") == 2.5
    assert coerce_number(3) == 3.0


def test_coerce_quality_defaults():
    assert coerce_quality(None) == 0
    assert coerce_quality("2") == 2
    assert coerce_quality(1) == 1
    assert coerce_quality("bad") == 0


def test_heatmap_missing_bins_remain_missing():
    values = bin_values(
        np.array([0.5, 2.5]), np.array([1.0, 3.0]),
        np.array([0.0, 1.0, 2.0, 3.0]),
    )
    assert np.isnan(values[1])
    z = rolling_zscore(values, 2)
    assert np.isnan(z[1])


def test_heatmap_sparse_observations_use_prior_observed_baseline():
    values = np.array([1.0, 2.0, np.nan, np.nan, 4.0])
    z = rolling_zscore(values, 2)
    assert np.isnan(z[2]) and np.isnan(z[3])
    assert np.isfinite(z[4]) and z[4] > 0
