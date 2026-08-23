"""Query logic: series queries, matrix/heatmap, data extraction."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from .downsample import downsample
from .downsample.rollup import aggregate_buckets, select_tier, tier_name
from .cache import GenericCache, RollupData
from .common import bin_values, coerce_number, coerce_quality, find_gap_intervals, rolling_zscore
from ..api.contracts import (
    CanonicalSeries, EntityPage, MatrixQuery, MatrixResult,
    QueryMetrics, QueryProvenance, SeriesPoints, SeriesQuery, SeriesQueryResponse,
)

# ── Helpers ───────────────────────────────────────────────────────────────

_r6 = lambda arr: np.round(arr, 6).tolist()

_ts = lambda value, fmt: (
    int(value * 1000) if fmt == "epoch_ms"
    else datetime.fromisoformat(str(value)).isoformat() if fmt == "iso"
    else float(value)
)

_parse_ts = lambda value, fmt: (
    None if value is None else
    datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc) if fmt == "epoch_ms" else
    (lambda p: p if p.tzinfo else p.replace(tzinfo=timezone.utc))(datetime.fromisoformat(str(value))) if fmt == "iso" else
    datetime.fromtimestamp(float(value), tz=timezone.utc)
)


def _get_path(obj: dict, path: str | None) -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _quality_flag(raw, quality_map: dict[str, str] | None) -> int:
    if raw is None:
        return 0
    if quality_map:
        c = quality_map.get(str(raw))
        if c is not None:
            return coerce_quality(c)
    return coerce_quality(raw)


# ── Data extraction ───────────────────────────────────────────────────────

def extract_rows(data: dict, config, measure_id: str
                 ) -> list[tuple[datetime, float | None, int]]:
    """Extract (timestamp, value, quality) from API response."""
    mapping = config.mapping
    spec = mapping.measures[measure_id]
    value_path = spec.value_path or measure_id
    fmt = mapping.timestamp_format
    quality_map = mapping.quality_map
    missing = mapping.missing_value
    rows: list[tuple[datetime, float | None, int]] = []

    if config.endpoints.series.shape == "columnar":
        t_arr = _get_path(data, mapping.timestamp)
        v_arr = _get_path(data, value_path)
        q_arr = _get_path(data, mapping.quality) if mapping.quality else None
        if not isinstance(t_arr, list) or not isinstance(v_arr, list):
            return rows
        for i in range(min(len(t_arr), len(v_arr))):
            ts = _parse_ts(t_arr[i], fmt)
            if ts is None:
                continue
            raw_v = v_arr[i]
            val = None if raw_v is None or (missing is not None and raw_v == missing) \
                else coerce_number(raw_v)
            rows.append((ts, val, _quality_flag(
                q_arr[i] if q_arr and i < len(q_arr) else None, quality_map)))
        return rows

    items = _get_path(data, config.endpoints.series.items_path)
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = _parse_ts(_get_path(item, mapping.timestamp), fmt)
        if ts is None:
            continue
        raw_v = _get_path(item, value_path)
        val = None if raw_v is None or (missing is not None and raw_v == missing) \
            else coerce_number(raw_v)
        rows.append((ts, val, _quality_flag(
            _get_path(item, mapping.quality) if mapping.quality else None, quality_map)))
    return rows


# ── Series query ──────────────────────────────────────────────────────────

async def run_query(adapter, request: SeriesQuery) -> SeriesQueryResponse:
    from .adapter import ConfigurableAdapter  # avoid circular
    assert isinstance(adapter, ConfigurableAdapter)
    t0 = time.perf_counter()
    _validate(adapter, request)
    start, end = _parse_ts(request.range.start, "unix"), _parse_ts(request.range.end, "unix")
    if end <= start:
        raise ValueError("range end must be after start")
    budget = request.resolution.pixel_width * request.resolution.points_per_pixel
    algo = request.downsampling

    hydrated, failed = {}, set()
    for eid in request.entity_ids:
        for mid in request.measure_ids:
            ok, src_ms = await adapter._hydrate(eid, mid, start, end)
            if ok:
                hydrated[(eid, mid)] = src_ms
            else:
                failed.add((eid, mid))

    tier = _pick_tier(adapter, (end - start).total_seconds(),
                      request.resolution.pixel_width, request.resolution.points_per_pixel)

    series = []
    for eid in request.entity_ids:
        for mid in request.measure_ids:
            if (eid, mid) in failed:
                series.append(CanonicalSeries(entity_id=eid, measure_id=mid, error="hydration failed"))
                continue
            spec = adapter.config.mapping.measures[mid]
            src_ms = hydrated[(eid, mid)]
            if tier == "raw":
                s = await _raw_series(adapter, eid, mid, spec, start, end, budget, algo, src_ms, t0)
            else:
                s = await _rollup_series(adapter, eid, mid, spec, start, end, tier, budget, algo, src_ms, t0)
            series.append(s)

    return SeriesQueryResponse(
        query=request, series=series,
        provenance=QueryProvenance(
            dataset_id=adapter.dataset_id, adapter="configurable",
            downsampling=algo, generated_at=datetime.now(timezone.utc).isoformat()))


def _pick_tier(adapter, span_s, pixel_width, points_per_pixel):
    levels = adapter._rollup_levels()
    if not levels:
        return "raw"
    tier = select_tier(span_s, pixel_width, points_per_pixel, levels=levels)
    return tier if isinstance(tier, (float, int)) else "raw"


def _validate(adapter, req: SeriesQuery):
    for mid in req.measure_ids:
        if mid not in adapter.config.mapping.measures:
            raise ValueError(f"no '{mid}' measure (available: {sorted(adapter.config.mapping.measures)})")
    if req.filters:
        raise ValueError("no filterable dimensions")


def _fidelity(adapter, *, tier, aggregated, truncated, **extra):
    return {"source": "http_cache", "tier": tier, "aggregated": aggregated,
            "truncated": truncated, "cache_namespace": adapter.dataset_key, **extra}


def _empty_series(adapter, eid, mid, spec, src_ms, t0, truncated=False, resolution="raw"):
    return CanonicalSeries(
        entity_id=eid, entity_label=adapter._labels.get(eid),
        measure_id=mid, unit=spec.unit, resolution=resolution, gaps=[],
        fidelity=_fidelity(adapter, tier=resolution, aggregated=resolution != "raw", truncated=truncated),
        metrics=QueryMetrics(query_ms=round((time.perf_counter() - t0) * 1000, 1),
                             source_ms=round(src_ms, 1), truncated=truncated))


# ── Raw series ────────────────────────────────────────────────────────────

async def _raw_series(adapter, eid, mid, spec, start, end, budget, algo, src_ms, t0):
    cache = adapter.cache
    total = await cache.count(eid, mid, start, end)
    if total == 0:
        return _empty_series(adapter, eid, mid, spec, src_ms, t0)

    if total > adapter.config.row_cap:
        span = (end - start).total_seconds()
        bkt = span / max(budget, 1)
        data = await cache.read_bucketed(eid, mid, bkt, start, end,
                                         max(adapter.config.row_cap, budget))
        if data is None or len(data.t) == 0:
            return _empty_series(adapter, eid, mid, spec, src_ms, t0)
        return _serve_bucketed(adapter, data, bkt, eid, mid, spec, algo, budget, src_ms, t0,
                               query_aggregated=True, rows_source=total)

    t, v, q, total, truncated = await cache.read(eid, mid, start, end, adapter.config.row_cap)
    if len(t) == 0:
        return _empty_series(adapter, eid, mid, spec, src_ms, t0, truncated)

    _learn_step(adapter, t)
    step = adapter.config.expected_step_seconds or adapter._inferred_step
    gaps = find_gap_intervals(t, step)
    q_summary = _quality_summary(q)
    payload = {"t": t, "avg": v, "sample_count": np.ones(len(t), np.int64), "quality_flag": q}
    sampled, ds_ms = downsample(payload, algo, budget)
    pts = SeriesPoints(t=_r6(sampled["t"]), value=_r6(sampled["avg"]),
                       quality=[int(x) for x in sampled["quality_flag"]],
                       sample_count=[int(x) for x in sampled["sample_count"]])
    ms = time.perf_counter()
    return CanonicalSeries(
        entity_id=eid, entity_label=adapter._labels.get(eid),
        measure_id=mid, unit=spec.unit, points=pts, resolution="raw",
        expected_step_seconds=step, gaps=gaps, quality_summary=q_summary,
        fidelity=_fidelity(adapter, tier="raw", aggregated=False, truncated=truncated),
        metrics=QueryMetrics(rows_source=total, rows_scanned=len(t), rows_returned=len(pts.t),
                             query_ms=round((ms - t0) * 1000, 1), source_ms=round(src_ms, 1),
                             downsample_ms=round(ds_ms, 2), truncated=truncated))


# ── Bucketed series ───────────────────────────────────────────────────────

def _serve_bucketed(adapter, data, bkt_s, eid, mid, spec, algo, budget,
                    src_ms, t0, *, query_aggregated=False, truncated=None, rows_source=None):
    gaps = find_gap_intervals(data.t, bkt_s)
    q_summary = _quality_summary(data.q)
    payload = {"t": data.t, "avg": data.avg, "min": data.min, "max": data.max,
               "first": data.first, "last": data.last,
               "min_t": data.min_t, "max_t": data.max_t,
               "first_t": data.first_t, "last_t": data.last_t,
               "sample_count": data.n, "quality_flag": data.q}
    sampled, ds_ms = downsample(payload, algo, budget)
    pts = SeriesPoints(
        t=_r6(sampled["t"]), value=_r6(sampled["avg"]),
        min=_r6(sampled["min"]), max=_r6(sampled["max"]),
        first=_r6(sampled["first"]), last=_r6(sampled["last"]),
        min_t=_r6(sampled["min_t"]), max_t=_r6(sampled["max_t"]),
        first_t=_r6(sampled["first_t"]), last_t=_r6(sampled["last_t"]),
        quality=[int(x) for x in sampled["quality_flag"]],
        sample_count=[int(x) for x in sampled["sample_count"]])
    truncated = truncated if truncated is not None else data.truncated
    rows_source = rows_source if rows_source is not None else data.total
    ms = time.perf_counter()
    return CanonicalSeries(
        entity_id=eid, entity_label=adapter._labels.get(eid),
        measure_id=mid, unit=spec.unit, points=pts,
        resolution=tier_name(bkt_s), expected_step_seconds=bkt_s,
        gaps=gaps, quality_summary=q_summary,
        fidelity=_fidelity(adapter, tier=tier_name(bkt_s), aggregated=True, truncated=truncated,
                           bucket_seconds=bkt_s, query_aggregated=query_aggregated),
        metrics=QueryMetrics(rows_source=rows_source, rows_scanned=len(data.t),
                             rows_returned=len(pts.t), query_ms=round((ms - t0) * 1000, 1),
                             source_ms=round(src_ms, 1), downsample_ms=round(ds_ms, 2),
                             truncated=truncated))


# ── Rollup series ─────────────────────────────────────────────────────────

async def _rollup_series(adapter, eid, mid, spec, start, end, bkt_s, budget, algo, src_ms, t0):
    cache = adapter.cache
    data = await cache.read_rollup(eid, mid, bkt_s, start, end, adapter.config.row_cap)
    truncated = False
    if data is None or len(data.t) == 0:
        t, v, q, _, truncated = await cache.read(eid, mid, start, end, adapter.config.row_cap)
        if len(t) == 0:
            return _empty_series(adapter, eid, mid, spec, src_ms, t0, truncated, resolution=tier_name(bkt_s))
        _learn_step(adapter, t)
        buckets = aggregate_buckets(t, v, q, [bkt_s])
        rows = buckets.get(bkt_s, [])
        if truncated:
            data = RollupData.from_rows(rows)
        else:
            if data and len(data.t):
                existing = set((data.t // bkt_s).astype(np.int64).tolist())
                rows = [r for r in rows if int(r[0] // bkt_s) not in existing]
            if rows:
                await cache.store_rollup(eid, mid, {bkt_s: rows})
                data = await cache.read_rollup(eid, mid, bkt_s, start, end, adapter.config.row_cap)
        if data is None or len(data.t) == 0:
            return _empty_series(adapter, eid, mid, spec, src_ms, t0, truncated, resolution=tier_name(bkt_s))
    return _serve_bucketed(adapter, data, bkt_s, eid, mid, spec, algo, budget, src_ms, t0,
                           query_aggregated=False, truncated=truncated)


# ── Matrix (heatmap) ──────────────────────────────────────────────────────

async def run_matrix(adapter, request: MatrixQuery) -> MatrixResult:
    if request.measure_id not in adapter.config.mapping.measures:
        raise ValueError(f"no '{request.measure_id}' measure")
    start = _parse_ts(request.range.start, "unix")
    end = _parse_ts(request.range.end, "unix")
    if (end - start).total_seconds() <= 0:
        raise ValueError("range end must be after start")

    ids = request.entity_ids[:200]
    for eid in ids:
        await adapter._hydrate(eid, request.measure_id, start, end)

    span = (end - start).total_seconds()
    edges = np.linspace(start.timestamp(), end.timestamp(), request.pixel_width + 1)
    centres = (edges[:-1] + edges[1:]) / 2.0
    cache = adapter.cache

    grid: list[list[float | None]] = []
    for eid in ids:
        total = await cache.count(eid, request.measure_id, start, end)
        if total == 0:
            grid.append([None] * request.pixel_width)
            continue
        if total > adapter.config.row_cap:
            bkt = span / max(request.pixel_width, 1)
            data = await cache.read_bucketed(eid, request.measure_id, bkt, start, end,
                                             max(adapter.config.row_cap, request.pixel_width))
            if data is None or len(data.t) == 0:
                grid.append([None] * request.pixel_width)
                continue
            t, v = data.t, data.avg
        else:
            t, v, _, _, _ = await cache.read(eid, request.measure_id, start, end, adapter.config.row_cap)
        z = rolling_zscore(bin_values(t, v, edges), 10)
        normed = (np.clip(z, -3.0, 3.0) + 3.0) / 6.0
        grid.append([round(float(x), 4) if np.isfinite(x) else None for x in normed])

    return MatrixResult(dataset_id=adapter.dataset_id, transform=request.transform,
                        t=centres.tolist(), entity_ids=ids, values=grid,
                        value_semantics="temporal_rolling_zscore",
                        fidelity={"cache_namespace": adapter.dataset_key})


# ── Shared helpers ────────────────────────────────────────────────────────

def _quality_summary(q: np.ndarray) -> dict[str, int]:
    return {str(int(f)): int(np.count_nonzero(q == f)) for f in np.unique(q)}


def _learn_step(adapter, t: np.ndarray):
    if adapter._inferred_step is None and len(t) > 1:
        diffs = np.diff(t)
        pos = diffs[diffs > 0]
        if len(pos):
            adapter._inferred_step = float(np.median(pos))
