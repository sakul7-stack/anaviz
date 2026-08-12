"""Configurable adapter tests: HTTP extraction, pagination, query/matrix paths,
gap preservation, and opaque-ID translation."""
import asyncio
from datetime import datetime, timezone

import httpx
import numpy as np
import pytest

from server.api.common import coverage_gaps, subtract_probed
from server.api.config import parse_config
from server.api.configurable import (
    ConfigurableHttpAdapter,
    extract_rows,
    format_request_timestamp,
    get_path,
    parse_source_timestamp,
)
from server.api.contracts import MatrixQuery, SeriesQuery


# ── Fake cache (same public interface the adapter uses) ──────────────────────

class FakeCache:
    def __init__(self, dataset_key="default:abc"):
        self.dataset_key = dataset_key
        self.series: dict[tuple[str, str], list[tuple]] = {}
        self.covered: list[tuple] = []

    async def coverage(self, entity_id, measure_id, start, end):
        return [(a, b) for e, m, a, b, status in self.covered
                if e == entity_id and m == measure_id
                and status == "queried" and b > start and a < end]

    async def store(self, entity_id, measure_id, rows, intervals, **kwargs):
        key = (entity_id, measure_id)
        bucket = self.series.setdefault(key, [])
        existing = {(r[0], r[1]) for r in bucket}
        for ts, value, quality in rows:
            if (ts, value) not in existing:
                bucket.append((ts, value, quality))
                existing.add((ts, value))
        bucket.sort(key=lambda r: r[0])
        for a, b, status in intervals:
            self.covered.append((entity_id, measure_id, a, b, status))

    async def read(self, entity_id, measure_id, start, end, cap):
        bucket = self.series.get((entity_id, measure_id), [])
        rows = [r for r in bucket if start <= r[0] < end]
        total = len(rows)
        truncated = total > cap
        rows = rows[:cap]
        t = np.array([r[0].timestamp() for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
        q = np.array([r[2] for r in rows], dtype=np.int16)
        return t, v, q, total, truncated

    async def count(self, entity_id, measure_id, start, end):
        bucket = self.series.get((entity_id, measure_id), [])
        return len([r for r in bucket if start <= r[0] < end])

    async def read_bucketed(self, entity_id, measure_id, bucket_s,
                            start, end, cap):
        from server.downsample.rollup import aggregate_buckets
        from server.api.configurable import (
            RollupData,
            bucket_rows_to_arrays,
        )
        bucket = self.series.get((entity_id, measure_id), [])
        rows = [r for r in bucket if start <= r[0] < end]
        if not rows:
            return RollupData.empty()
        t = np.array([r[0].timestamp() for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
        q = np.array([r[2] for r in rows], dtype=np.int16)
        agg = aggregate_buckets(t, v, q, [float(bucket_s)])
        return bucket_rows_to_arrays(agg.get(float(bucket_s), []))

    async def drop_dataset(self):
        self.series.clear()
        self.covered.clear()


# ── Minimal config for a datasource-shaped API ───────────────────────────────

ARCHIVE_CONFIG = {
    "version": 1,
    "dataset": {"id": "default", "label": "Archive"},
    "source": {"type": "http", "base_url": "http://datasource:9000"},
    "endpoints": {
        "entities": {"path": "/archive/channels"},
        "extent": {"path": "/archive/extent", "start_path": "t_min", "end_path": "t_max"},
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
    "pagination": {"type": "offset", "page_size": 2, "max_pages": 10},
    "row_cap": 1000,
}

# Source data: element 10 has a gap between t=40 and t=90
SOURCE_ROWS = {
    10: [(t, float(t), 0) for t in range(0, 41, 10)] +
        [(t, float(t), 1) for t in range(90, 141, 10)],
    20: [(t, 5.0, 0) for t in range(0, 141, 20)],
}
EXTENT = {"t_min": 0.0, "t_max": 140.0}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = request.url.params
    if path == "/archive/channels":
        return httpx.Response(200, json=[
            {"element_id": 10, "name": "sensor-a"},
            {"element_id": 20, "name": "sensor-b"},
        ])
    if path == "/archive/extent":
        return httpx.Response(200, json=EXTENT)
    if path == "/archive/eventhistory":
        eid = int(params["element_id"])
        t0 = float(params["t0"])
        t1 = float(params["t1"])
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 2))
        page = [(t, v, q) for t, v, q in SOURCE_ROWS[eid] if t0 <= t < t1]
        chunk = page[offset:offset + limit]
        next_offset = offset + len(chunk) if offset + len(chunk) < len(page) else None
        return httpx.Response(200, json={
            "element_id": eid,
            "rows": len(chunk),
            "next_offset": next_offset,
            "t": [r[0] for r in chunk],
            "value": [r[1] for r in chunk],
            "quality_flag": [r[2] for r in chunk],
        })
    return httpx.Response(404)


def _adapter(cache=None, config=None):
    cfg = parse_config(config or ARCHIVE_CONFIG)
    return ConfigurableHttpAdapter(
        cfg, cache or FakeCache(),
        transport=httpx.MockTransport(_handler))


def _dt(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


# ── Extraction helpers ────────────────────────────────────────────────────────

def test_get_path_dotted():
    assert get_path({"a": {"b": [1]}}, "a.b") == [1]
    assert get_path({"a": 1}, "x.y") is None
    assert get_path({"a": 1}, None) == {"a": 1}


def test_parse_source_timestamp_formats():
    assert parse_source_timestamp(100.0, "unix") == _dt(100)
    assert parse_source_timestamp("100", "unix") == _dt(100)
    assert parse_source_timestamp(100_000, "epoch_ms") == _dt(100)
    assert parse_source_timestamp(
        "2026-01-01T00:01:40+00:00", "iso") == datetime(2026, 1, 1, 0, 1, 40,
                                                         tzinfo=timezone.utc)
    assert parse_source_timestamp(None, "unix") is None
    assert parse_source_timestamp("nope", "unix") is None


def test_format_request_timestamp_is_symmetric():
    assert format_request_timestamp(_dt(100), "unix") == 100.0
    assert format_request_timestamp(_dt(100), "epoch_ms") == 100_000
    assert format_request_timestamp(_dt(100), "iso") == "1970-01-01T00:01:40+00:00"


def test_extract_rows_columnar():
    config = parse_config(ARCHIVE_CONFIG)
    data = {
        "t": [0.0, 10.0, 20.0],
        "value": [1.0, None, 3.0],
        "quality_flag": [0, 2, 0],
        "next_offset": None,
    }
    rows = extract_rows(data, config, "value")
    assert len(rows) == 3
    ts, value, quality = rows[1]
    assert value is None and quality == 2  # missing value preserved as None


def test_extract_rows_row_shape():
    config = parse_config({
        "version": 1,
        "dataset": {"id": "s", "label": "S"},
        "source": {"type": "http", "base_url": "http://x"},
        "endpoints": {
            "entities": {"path": "/e"},
            "extent": {"path": "/x", "start_path": "a", "end_path": "b"},
            "series": {"path": "/s", "shape": "row", "items_path": "data"},
        },
        "mapping": {
            "entity_id": "id",
            "timestamp": "ts",
            "timestamp_format": "epoch_ms",
            "measures": {"temp": {"value_path": "temperature"}},
            "quality": "q",
            "missing_value": -9999,
        },
    })
    data = {"data": [
        {"id": 1, "ts": 100_000, "temperature": 21.5, "q": "OK"},
        {"id": 1, "ts": 200_000, "temperature": -9999, "q": "BAD"},
    ]}
    rows = extract_rows(data, config, "temp")
    assert len(rows) == 2
    assert rows[0][0] == _dt(100)
    assert rows[1][1] is None          # sentinel became a missing sample
    assert rows[0][2] == 0             # un-mapped quality defaults to 0


# ── Adapter behavior ──────────────────────────────────────────────────────────

def test_describe_builds_canonical_schema():
    schema = asyncio.run(_adapter().describe())
    assert schema.id == "default"
    assert [m.id for m in schema.measures] == ["value"]
    assert "element_id" not in str(schema)
    assert "matrix" in schema.capabilities


def test_entities_namespace_and_page():
    adapter = _adapter()
    page = asyncio.run(adapter.entities(search="sensor", offset=1, limit=1))
    assert page.total == 2
    assert page.items[0].id == "default:20"
    assert page.items[0].label == "sensor-b"


def test_extent_reads_endpoint():
    result = asyncio.run(_adapter().extent())
    assert result.start == 0.0 and result.end == 140.0


def _request(**updates):
    data = {
        "dataset_id": "default",
        "entity_ids": ["default:10"],
        "measure_ids": ["value"],
        "range": {"start": 0, "end": 140},
        "resolution": {"strategy": "auto", "pixel_width": 800,
                       "points_per_pixel": 2},
        "downsampling": "M4",
    }
    data.update(updates)
    return SeriesQuery(**data)


def test_query_hydrates_preserves_gaps_and_metadata():
    adapter = _adapter()
    response = asyncio.run(adapter.query(_request()))
    result = response.series[0]
    assert result.entity_id == "default:10"
    assert result.measure_id == "value"
    assert result.gaps == [[40.0, 90.0]]
    assert result.points.value  # non-empty downsampled series
    assert result.quality_summary.get("1") == 5
    assert result.fidelity["missing_intervals_preserved"] is True
    assert result.metrics.rows_source == 10
    assert response.provenance.adapter == "configurable"
    # second call must hydrate from cache (coverage recorded on first pass)
    second = asyncio.run(adapter.query(_request()))
    assert second.series[0].gaps == [[40.0, 90.0]]


def test_query_rejects_unknown_measure_and_opaque_id():
    adapter = _adapter()
    with pytest.raises(ValueError, match="no 'temperature' measure"):
        asyncio.run(adapter.query(_request(measure_ids=["temperature"])))
    with pytest.raises(ValueError, match="unknown default entity ID"):
        asyncio.run(adapter.query(_request(entity_ids=["10"])))


def test_query_skips_irregular_missing_values_never_fills():
    adapter = _adapter()
    response = asyncio.run(adapter.query(_request(
        entity_ids=["default:20"], range={"start": 0, "end": 140})))
    result = response.series[0]
    assert result.points.value
    # no forward-filled points can exist at times the source never sampled
    assert None not in result.points.value  # downsample output has no nulls


def test_matrix_preserves_missing_bins():
    adapter = _adapter()
    request = MatrixQuery(
        dataset_id="default",
        entity_ids=["default:10", "default:20"],
        measure_id="value",
        range={"start": 0, "end": 140},
        pixel_width=14,
    )
    result = asyncio.run(adapter.matrix(request))
    assert result.transform == "temporal_rolling_zscore"
    assert result.entity_ids == ["default:10", "default:20"]
    assert len(result.values) == 2 and len(result.values[0]) == 14
    assert result.fidelity["missing_bins_preserved"] is True
    # the gap bucket (50–60s) for entity 10 is NaN → None
    assert result.values[0][5] is None
    # entity 20 samples at 60s (bucket 6), so its gap bucket stays None too
    assert result.values[1][5] is None
    assert result.values[1][6] is not None


def test_matrix_rejects_unsupported_measure():
    adapter = _adapter()
    request = MatrixQuery(
        dataset_id="default", entity_ids=["default:10"],
        measure_id="nope", range={"start": 0, "end": 140}, pixel_width=10)
    with pytest.raises(ValueError, match="no 'nope' measure"):
        asyncio.run(adapter.matrix(request))


# ── Coverage arithmetic (reused from common) ─────────────────────────────────

def test_coverage_gaps_preserve_internal_source_gaps():
    gaps = coverage_gaps(_dt(0), _dt(30),
                         [(_dt(0), _dt(10)), (_dt(20), _dt(30))])
    assert gaps == [(_dt(10), _dt(20))]


def test_subtract_probed_removes_known_empty_ranges():
    gaps = subtract_probed(
        _dt(0), _dt(50),
        [(_dt(10), _dt(20)), (_dt(30), _dt(40))])
    assert gaps == [(_dt(0), _dt(10)), (_dt(20), _dt(30)), (_dt(40), _dt(50))]
