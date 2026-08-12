"""Rollup tier tests: tier selection math, numpy bucket aggregation, and the
adapter's on-demand rollup query path (resolution/gaps/metrics fidelity)."""
import asyncio
import math
from datetime import datetime, timezone

import httpx
import numpy as np
import pytest

from server.api.config import parse_config
from server.api.configurable import (
    ConfigurableHttpAdapter,
    RollupData,
    bucket_rows_to_arrays,
)
from server.api.contracts import SeriesQuery
from server.downsample.rollup import (
    aggregate_buckets,
    select_tier,
    tier_name,
)
from tests.test_configurable import FakeCache


# ── Tier selection ───────────────────────────────────────────────────────────

def test_select_tier_without_levels_always_raw():
    # Tiers are strictly opt-in: no levels means no rollup, ever.
    assert select_tier(36000, 500, 2) == "raw"
    assert select_tier(1_000_000, 1000, 2, None) == "raw"
    assert select_tier(0, 100, 2, [16, 64]) == "raw"


def test_select_tier_adapts_to_span_and_pixels():
    levels = [1.0, 4.0, 16.0, 64.0, 256.0]
    # tiny span: raw (target 0.0875s < 4x finest tier)
    assert select_tier(140, 800, 2, levels=[10.0, 40.0]) == "raw"
    # 10h at 1 Hz, px 500 x2 -> target 36s -> coarsest level <= 36 is 16s
    assert select_tier(36000, 500, 2, levels=levels) == 16.0
    # huge span -> coarser tier (target 500s -> 256s)
    assert select_tier(1_000_000, 1000, 2, levels=levels) == 256.0


def test_select_tier_honors_explicit_levels():
    # target 1800s -> coarsest override level <= 1800 is 900s
    assert select_tier(360000, 100, 2, levels=[60, 300, 900]) == 900.0
    # target 36s < 4*60 -> raw when the finest override is too coarse
    assert select_tier(36000, 500, 2, levels=[60, 300]) == "raw"
    # target 100s >= 4*16 -> 64s bucket
    assert select_tier(100000, 500, 2, levels=[16, 64]) == 64.0


def test_tier_name_human_labels():
    assert tier_name(16) == "16s"
    assert tier_name(60) == "1m"
    assert tier_name(300) == "5m"
    assert tier_name(3600) == "1h"
    assert tier_name(86400) == "1d"
    assert tier_name(4096) == "1h8m16s"


# ── Bucket aggregation ───────────────────────────────────────────────────────

def test_aggregate_buckets_stats():
    t = np.array([0, 1, 2, 3, 10, 11, 20], dtype=float)
    v = np.array([5, 1, 9, 3, 2, 8, 7], dtype=float)
    q = np.array([0, 2, 0, 1, 0, 0, 3], dtype=np.int16)
    result = aggregate_buckets(t, v, q, [4.0])
    buckets = result[4.0]
    assert len(buckets) == 3
    b0, b8, b16 = buckets
    assert b0[:2] == (0.0, 5.0) and b0[2] == 0.0          # first 5 @ ts 0
    assert b0[3] == 1.0 and b0[4] == 1.0                  # min 1 @ ts 1
    assert b0[5] == 9.0 and b0[6] == 2.0                  # max 9 @ ts 2
    assert b0[7] == 3.0 and b0[8] == 3.0                  # last 3 @ ts 3
    assert b0[9] == pytest.approx(4.5) and b0[10] == 4    # avg, count
    assert b0[11] == 2                                    # q_worst
    assert b8[0] == 8.0 and b8[10] == 2 and b8[11] == 0
    assert b16[10] == 1 and b16[11] == 3


def test_aggregate_buckets_tie_breaking():
    t = np.array([0, 1, 2, 3, 4, 5, 6, 7], dtype=float)
    v = np.array([4, 2, 5, 2, 5, 1, 5, 0], dtype=float)
    q = np.zeros(8, dtype=np.int16)
    buckets = aggregate_buckets(t, v, q, [4.0])[4.0]
    # bucket 0: min 2 first occurs at ts 1 -> min_ts 1 (earliest tie)
    assert buckets[0][3] == 2.0 and buckets[0][4] == 1.0
    # bucket 1: max 5 occurs at ts 4 and ts 6 -> max_ts 6 (latest tie)
    assert buckets[1][5] == 5.0 and buckets[1][6] == 6.0


def test_aggregate_buckets_empty():
    assert aggregate_buckets(np.array([]), np.array([]),
                             np.array([], dtype=np.int16), [4.0]) == {}


def test_bucket_rows_to_arrays_roundtrip():
    t = np.arange(0, 100, dtype=float)
    v = np.sin(t / 7)
    q = np.full(100, 2, dtype=np.int16)
    rows = aggregate_buckets(t, v, q, [16.0])[16.0]
    data = bucket_rows_to_arrays(rows)
    assert data is not None
    assert len(data.t) == 7
    assert data.total == 100
    assert (data.q == 2).all()
    assert (data.n == np.array([16, 16, 16, 16, 16, 16, 4])).all()
    assert bucket_rows_to_arrays([]) is None
    empty = RollupData.empty()
    assert empty.total == 0 and len(empty.t) == 0


# ── Adapter rollup query path ────────────────────────────────────────────────

DENSE_EXTENT = {"t_min": 0.0, "t_max": 36000.0}


def _dense_rows():
    rows = []
    for ts in range(0, 36000):
        if 5000 <= ts < 5100:      # one source gap
            continue
        rows.append((ts, round(math.sin(ts / 50) * 100, 3),
                     2 if ts % 60 == 0 else 0))
    return rows


def _dense_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = request.url.params
    if path == "/archive/channels":
        return httpx.Response(200, json=[
            {"element_id": 10, "name": "sensor-a"}])
    if path == "/archive/extent":
        return httpx.Response(200, json=DENSE_EXTENT)
    if path == "/archive/eventhistory":
        eid = int(params["element_id"])
        t0 = float(params["t0"])
        t1 = float(params["t1"])
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 20000))
        page = [(t, v, q) for t, v, q in _dense_rows() if t0 <= t < t1]
        chunk = page[offset:offset + limit]
        next_offset = (offset + len(chunk)
                       if offset + len(chunk) < len(page) else None)
        return httpx.Response(200, json={
            "element_id": eid,
            "rows": len(chunk),
            "next_offset": next_offset,
            "t": [r[0] for r in chunk],
            "value": [r[1] for r in chunk],
            "quality_flag": [r[2] for r in chunk],
        })
    return httpx.Response(404)


class FakeRollupCache(FakeCache):
    """Rollup rows are derived from stored raw rows (mirrors the SQL merge)."""

    async def store_rollup(self, entity_id, measure_id, bucket_rows):
        pass

    async def read_rollup(self, entity_id, measure_id, bucket_s,
                          start, end, cap):
        bucket = self.series.get((entity_id, measure_id), [])
        rows = [r for r in bucket if start <= r[0] < end]
        if not rows:
            return RollupData.empty()
        t = np.array([r[0].timestamp() for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
        q = np.array([r[2] for r in rows], dtype=np.int16)
        agg = aggregate_buckets(t, v, q, [float(bucket_s)])
        return bucket_rows_to_arrays(agg[bucket_s])


class LegacyCache(FakeCache):
    """Cache written before rollups existed: raw rows, no bucket rows."""

    def __init__(self):
        super().__init__()
        self.rollup_stores = 0
        self.rollup_reads = 0
        self.buckets: dict[tuple, list] = {}

    async def read_rollup(self, entity_id, measure_id, bucket_s,
                          start, end, cap):
        self.rollup_reads += 1
        rows = self.buckets.get((entity_id, measure_id, float(bucket_s)), [])
        rows = [r for r in rows if start.timestamp() <= r[0] < end.timestamp()]
        if not rows:
            return RollupData.empty()
        return bucket_rows_to_arrays(rows)

    async def store_rollup(self, entity_id, measure_id, bucket_rows):
        self.rollup_stores += 1
        for bucket_s, rows in bucket_rows.items():
            key = (entity_id, measure_id, float(bucket_s))
            self.buckets.setdefault(key, []).extend(rows)
            self.buckets[key].sort(key=lambda r: r[0])

    async def forget_rollup(self):
        self.buckets.clear()


DENSE_CONFIG = {
    "version": 1,
    "dataset": {"id": "default", "label": "Archive"},
    "source": {"type": "http", "base_url": "http://datasource:9000"},
    "endpoints": {
        "entities": {"path": "/archive/channels"},
        "extent": {"path": "/archive/extent", "start_path": "t_min",
                   "end_path": "t_max"},
        "series": {
            "path": "/archive/eventhistory",
            "shape": "columnar",
            "parameters": {"entity": "element_id", "start": "t0", "end": "t1"},
            "offset_param": "offset",
            "limit_param": "limit",
            "next_offset_path": "next_offset",
        },
    },
    "mapping": {
        "entity_id": "element_id",
        "entity_label": "name",
        "timestamp": "t",
        "timestamp_format": "unix",
        "measures": {"value": {"value_path": "value"}},
        "quality": "quality_flag",
    },
    "pagination": {"type": "offset", "page_size": 20000, "max_pages": 10},
    "row_cap": 100_000,
    "rollup_enabled": True,
    "rollup_levels": [1, 4, 16, 64, 256],
}


def _adapter(cache=None, config=None):
    cfg = parse_config(config or DENSE_CONFIG)
    return ConfigurableHttpAdapter(
        cfg, cache or FakeRollupCache(),
        transport=httpx.MockTransport(_dense_handler))


def _big_request():
    return SeriesQuery(
        dataset_id="default",
        entity_ids=["default:10"],
        measure_ids=["value"],
        range={"start": 0, "end": 36000},
        resolution={"strategy": "auto", "pixel_width": 500,
                    "points_per_pixel": 2},
        downsampling="M4",
    )


def test_query_uses_rollup_tier_for_big_range():
    adapter = _adapter()
    response = asyncio.run(adapter.query(_big_request()))
    result = response.series[0]
    assert result.resolution == "16s"
    assert result.expected_step_seconds == 16.0
    assert result.fidelity["tier"] == "16s"
    assert result.fidelity["aggregated"] is True
    assert result.points.value  # non-empty downsampled series
    # M4 fits the pixel budget (500px x 2)
    assert len(result.points.t) <= 1000
    # raw rows are counted via bucket n; cache reads stayed small
    assert result.metrics.rows_source == 35900
    assert result.metrics.rows_scanned < result.metrics.rows_source
    # bucket sample counts reflect the 1 Hz source
    assert max(result.points.sample_count) >= 10
    # quality flag 2 (every 60th sample) survives as q_worst
    assert result.quality_summary.get("2", 0) > 0
    # the source gap survives bucketing (bucket-aligned edges)
    assert len(result.gaps) == 1
    assert result.gaps[0][0] <= 5005 <= result.gaps[0][1]
    # enriched corner arrays are present for band downsampling
    assert result.points.min is not None and result.points.max is not None


def test_query_rollup_replays_from_cache():
    cache = FakeRollupCache()
    adapter = _adapter(cache)
    asyncio.run(adapter.query(_big_request()))
    second = asyncio.run(adapter.query(_big_request()))
    result = second.series[0]
    assert result.resolution == "16s"
    assert result.metrics.rows_source == 35900
    assert result.points.value == asyncio.run(
        adapter.query(_big_request())).series[0].points.value


def test_query_backfills_legacy_cache_without_rollup_rows():
    cache = LegacyCache()
    adapter = _adapter(cache)
    result = asyncio.run(adapter.query(_big_request())).series[0]
    # served from the one-time in-memory backfill, persisted for later queries
    assert result.resolution == "16s"
    assert result.points.value
    assert result.metrics.rows_source == 35900
    assert cache.rollup_stores == 1


def test_query_backfill_truncated_never_persists_partial_rollup():
    config = dict(DENSE_CONFIG)
    config["row_cap"] = 5000          # read cap smaller than stored raw rows
    cache = LegacyCache()
    # a legacy cache holding more raw rows than the current read cap
    key = ("default:10", "value")
    cache.series[key] = [
        (datetime.fromtimestamp(t, tz=timezone.utc), v, q)
        for t, v, q in _dense_rows()]
    cache.series[key].sort(key=lambda r: r[0])
    adapter = _adapter(cache, config=config)
    result = asyncio.run(adapter.query(_big_request())).series[0]
    assert result.resolution == "16s"
    assert result.metrics.truncated is True
    assert result.fidelity["truncated"] is True
    assert cache.rollup_stores == 0    # partial buckets must not be persisted


def test_backfill_never_double_counts_existing_buckets():
    """A legacy cache (raw rows without buckets) backfills exactly once:
    re-running the query must not re-aggregate and double-count the stored
    bucket rows."""
    cache = LegacyCache()
    adapter = _adapter(cache)
    # hydrate + first rollup-tier query (backfills and persists the rollup)
    first = asyncio.run(adapter.query(_big_request()))
    assert first.series[0].resolution == "16s"
    assert cache.rollup_stores == 1
    # second query reads the persisted buckets; nothing new to store
    second = asyncio.run(adapter.query(_big_request()))
    assert second.series[0].resolution == "16s"
    assert second.series[0].points.value
    assert cache.rollup_stores == 1
    # raw count stays exact — nothing was double-counted
    assert second.series[0].metrics.rows_source == 35900


def test_query_small_range_stays_raw():
    adapter = _adapter()
    request = SeriesQuery(
        dataset_id="default",
        entity_ids=["default:10"],
        measure_ids=["value"],
        range={"start": 0, "end": 140},
        resolution={"strategy": "auto", "pixel_width": 800,
                    "points_per_pixel": 2},
        downsampling="M4",
    )
    result = asyncio.run(adapter.query(request)).series[0]
    assert result.resolution == "raw"
    assert result.fidelity["aggregated"] is False


def test_query_without_rollup_opt_in_stays_raw_even_for_big_ranges():
    """No rollup_levels in the config: the biggest range is still served from
    raw rows — nothing is auto-derived from the sampling step."""
    config = {k: v for k, v in DENSE_CONFIG.items()
              if k not in ("rollup_enabled", "rollup_levels")}
    adapter = _adapter(config=config)
    result = asyncio.run(adapter.query(_big_request())).series[0]
    assert result.resolution == "raw"
    assert result.fidelity["aggregated"] is False
    assert result.metrics.rows_scanned == result.metrics.rows_source
    assert result.metrics.rows_source == 35900


def test_query_rollup_levels_alone_opts_in():
    """Declaring rollup_levels (without touching rollup_enabled) is the
    opt-in: the dataset gets exactly those tiers, nothing derived."""
    config = {k: v for k, v in DENSE_CONFIG.items()
              if k != "rollup_enabled"}
    adapter = _adapter(config=config)
    result = asyncio.run(adapter.query(_big_request())).series[0]
    assert result.resolution == "16s"
    assert result.fidelity["aggregated"] is True


def test_query_rollup_honors_config_override_levels():
    config = dict(DENSE_CONFIG)
    config["rollup_levels"] = [64, 256, 1024]
    adapter = _adapter(config=config)
    # target 36s < 4*64 -> raw under the explicit (coarser) tiers
    request = _big_request()
    result = asyncio.run(adapter.query(request)).series[0]
    assert result.resolution == "raw"

    # a much bigger span lands on the 256s tier
    request = SeriesQuery(
        dataset_id="default",
        entity_ids=["default:10"],
        measure_ids=["value"],
        range={"start": 0, "end": 1_000_000},
        resolution={"strategy": "auto", "pixel_width": 500,
                    "points_per_pixel": 2},
        downsampling="M4",
    )
    result = asyncio.run(adapter.query(request)).series[0]
    assert result.resolution == "4m16s"   # tier_name(256)
    assert result.expected_step_seconds == 256.0


def test_config_rollup_levels_validation():
    config = parse_config({**DENSE_CONFIG, "rollup_levels": [300, 60]})
    assert config.rollup_levels == [60, 300]   # sorted
    assert config.rollup_enabled is True       # kill-switch defaults on
    # declaring levels opts in; omitting them entirely means raw-only
    bare = parse_config({k: v for k, v in DENSE_CONFIG.items()
                         if k not in ("rollup_enabled", "rollup_levels")})
    assert bare.rollup_enabled is True and bare.rollup_levels is None
    assert parse_config({**DENSE_CONFIG, "rollup_enabled": False}).rollup_enabled is False
    with pytest.raises(ValueError, match="positive"):
        parse_config({**DENSE_CONFIG, "rollup_levels": [60, -5]})
    with pytest.raises(ValueError, match="at most 12"):
        parse_config({**DENSE_CONFIG, "rollup_levels": list(range(1, 14))})


def test_rollup_can_be_disabled_per_dataset():
    """rollup_enabled=false must serve every range from raw rows: no tiers,
    no rollup storage, no missing-range backfill tracking."""
    config = {**DENSE_CONFIG, "rollup_enabled": False,
              "rollup_levels": [16, 64]}      # override ignored when off
    cache = LegacyCache()
    adapter = _adapter(cache, config=config)
    result = asyncio.run(adapter.query(_big_request())).series[0]
    assert result.resolution == "raw"
    assert result.fidelity["aggregated"] is False
    assert result.metrics.rows_source == 35900
    assert result.metrics.rows_scanned == result.metrics.rows_source  # raw read
    assert cache.rollup_stores == 0             # nothing was aggregated
    assert cache.rollup_reads == 0              # rollup never consulted
    # and zooming never flips to a tier either
    request = SeriesQuery(
        dataset_id="default", entity_ids=["default:10"], measure_ids=["value"],
        range={"start": 0, "end": 1_000_000},
        resolution={"strategy": "auto", "pixel_width": 500,
                    "points_per_pixel": 2},
        downsampling="M4")
    result = asyncio.run(adapter.query(request)).series[0]
    assert result.resolution == "raw"


# ── No-data-loss dense raw ranges ──────────────────────────────────────────

def test_query_dense_raw_range_never_truncates():
    """A raw-mode range denser than row_cap must be aggregated in the DB over
    ALL rows (one bucket per pixel) — never cut off at the cap."""
    config = {k: v for k, v in DENSE_CONFIG.items()
              if k not in ("rollup_enabled", "rollup_levels")}
    config["row_cap"] = 5000          # dense range far exceeds the cap
    cache = LegacyCache()
    # pre-seed a cache holding far more rows than the read cap (dense range)
    key = ("default:10", "value")
    cache.series[key] = [
        (datetime.fromtimestamp(t, tz=timezone.utc), v, q)
        for t, v, q in _dense_rows()]
    cache.series[key].sort(key=lambda r: r[0])
    cache.covered.append(("default:10", "value",
                          _dt(0), _dt(36000), "queried"))
    adapter = _adapter(cache, config=config)
    result = asyncio.run(adapter.query(_big_request())).series[0]
    # served aggregated from the full range, not truncated raw rows
    assert result.resolution != "raw"
    assert result.fidelity["aggregated"] is True
    assert result.fidelity["query_aggregated"] is True
    assert result.fidelity["truncated"] is False
    assert result.metrics.truncated is False
    # every source row contributed — rows_source is the FULL count
    assert result.metrics.rows_source == 35900
    # the bucketed read is much smaller than the raw row count
    assert result.metrics.rows_scanned < result.metrics.rows_source
    # the source gap survives bucketing
    assert len(result.gaps) >= 1
    assert any(lo <= 5005 <= hi for lo, hi in result.gaps)


def test_query_small_raw_range_below_cap_stays_raw():
    """Ranges within row_cap keep the exact raw path — no aggregation."""
    config = {k: v for k, v in DENSE_CONFIG.items()
              if k not in ("rollup_enabled", "rollup_levels")}
    adapter = _adapter(config=config)   # row_cap 100k > 35.9k rows
    result = asyncio.run(adapter.query(_big_request())).series[0]
    assert result.resolution == "raw"
    assert result.fidelity["aggregated"] is False
    assert result.fidelity["truncated"] is False
    assert result.metrics.rows_scanned == result.metrics.rows_source == 35900


def test_matrix_dense_range_never_truncates():
    """Matrix view on a dense range must also use the full-range aggregation."""
    from server.api.contracts import MatrixQuery
    config = {k: v for k, v in DENSE_CONFIG.items()
              if k not in ("rollup_enabled", "rollup_levels")}
    config["row_cap"] = 5000
    cache = LegacyCache()
    key = ("default:10", "value")
    cache.series[key] = [
        (datetime.fromtimestamp(t, tz=timezone.utc), v, q)
        for t, v, q in _dense_rows()]
    cache.series[key].sort(key=lambda r: r[0])
    cache.covered.append(("default:10", "value",
                          _dt(0), _dt(36000), "queried"))
    adapter = _adapter(cache, config=config)
    request = MatrixQuery(
        dataset_id="default", entity_ids=["default:10"],
        measure_id="value", range={"start": 0, "end": 36000},
        pixel_width=50,
    )
    result = asyncio.run(adapter.matrix(request))
    assert len(result.values) == 1 and len(result.values[0]) == 50
    # cells across the full range are scored (no truncated tail of None bins)
    scored = sum(1 for v in result.values[0] if v is not None)
    assert scored > 40


def test_read_bucketed_epoch_anchor_negative_timestamps():
    """Buckets are anchored at the Unix epoch even before 1970: floor
    division must agree between the SQL path and numpy aggregation."""
    from server.api.configurable import bucket_rows_to_arrays
    cache = LegacyCache()
    key = ("default:10", "value")
    cache.series[key] = [
        (datetime.fromtimestamp(t, tz=timezone.utc), float(t), 0)
        for t in range(-300, 0, 1)]     # -300s .. -1s (pre-epoch)
    rows = asyncio.run(cache.read_bucketed(
        "default:10", "value", 60.0, _dt(-300), _dt(0), 1000))
    assert rows is not None and len(rows.t) == 5   # buckets at -300,-240,...
    # bucket first_ts anchored at floor(-300/60)*60 = -300
    assert rows.t[0] == -300.0
    # every row contributed exactly once
    assert rows.total == 300


def test_query_dense_rows_source_exact_at_cap():
    """rows_source must report the true cached row count even when the bucket
    LIMIT (cap) is hit — never a partial sum of returned buckets."""
    config = {k: v for k, v in DENSE_CONFIG.items()
              if k not in ("rollup_enabled", "rollup_levels")}
    config["row_cap"] = 5000
    cache = LegacyCache()
    key = ("default:10", "value")
    cache.series[key] = [
        (datetime.fromtimestamp(t, tz=timezone.utc), v, q)
        for t, v, q in _dense_rows()]
    cache.series[key].sort(key=lambda r: r[0])
    cache.covered.append(("default:10", "value",
                          _dt(0), _dt(36000), "queried"))
    adapter = _adapter(cache, config=config)
    result = asyncio.run(adapter.query(_big_request())).series[0]
    assert result.metrics.rows_source == 35900   # full count, not buckets' sum


def _dt(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc)
