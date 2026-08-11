"""Config-driven HTTP adapter: turns a validated ``config.json`` + datasource URL
into the canonical ``DatasetAdapter`` contract.

Nothing is preloaded: the project ships empty and the user registers their own
API + ``config.json`` via ``POST /api/configs`` (or a config dropped into
``DCSVIZ_CONFIG_DIR``). There is no HLT-specific code path in this project.

Data path:
    browser --canonical--> adapter --HTTP--> user datasource
                              |
                              +-> GenericDatasetCache (fixed-schema Postgres
                                  namespace keyed by dataset fingerprint)
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import numpy as np
from psycopg_pool import AsyncConnectionPool

from ..downsample import downsample
from .common import (
    bin_values,
    coerce_number,
    coerce_quality,
    coverage_gaps,
    find_gap_intervals,
    parse_timestamp,
    rolling_zscore,
)
from .config import DatasetConfig
from .contracts import (
    CanonicalSeries,
    DatasetAdapter,
    DatasetSchema,
    EntityPage,
    EntityRecord,
    MeasureSchema,
    MatrixQuery,
    MatrixResult,
    QueryMetrics,
    QueryProvenance,
    SeriesPoints,
    SeriesQuery,
    SeriesQueryResponse,
    TimeExtent,
)

_ROLLING_WINDOW = 10
_ENTITY_FETCH_CAP = 100_000


# ── JSON-path helper ──────────────────────────────────────────────────────────

def get_path(obj: dict, path: str | None):
    """Walk a dotted JSON path ("data.items"); None path returns obj itself."""
    if not path:
        return obj
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


# ── Timestamp parsing / formatting ───────────────────────────────────────────

def format_request_timestamp(dt: datetime, fmt: str) -> Any:
    """Serialize a request boundary the way the source expects."""
    if fmt == "epoch_ms":
        return int(dt.timestamp() * 1000)
    if fmt == "iso":
        return dt.isoformat()
    return dt.timestamp()


def parse_source_timestamp(value: Any, fmt: str) -> datetime | None:
    """Convert an upstream timestamp to an aware UTC datetime."""
    if value is None:
        return None
    try:
        if fmt == "epoch_ms":
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        if fmt == "iso":
            text = str(value)
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        # unix (seconds, float or numeric string)
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


# ── Response extraction ───────────────────────────────────────────────────────

def extract_rows(
    data: dict[str, Any],
    config: DatasetConfig,
    measure_id: str,
) -> list[tuple[datetime, float | None, int]]:
    """Extract (ts, value, quality) rows from a series response.

    Row shape: ``items`` is a list of objects, each carrying its own timestamp,
    value (at ``value_path`` or the measure id), and optional quality.
    Columnar shape: the response carries parallel arrays ``t`` / value / quality.
    """
    series = config.endpoints.series
    mapping = config.mapping
    spec = mapping.measures[measure_id]
    value_path = spec.value_path or measure_id
    missing = mapping.missing_value
    quality_path = mapping.quality
    quality_map = mapping.quality_map
    fmt = mapping.timestamp_format

    rows: list[tuple[datetime, float | None, int]] = []
    if series.shape == "columnar":
        t_arr = get_path(data, mapping.timestamp)
        v_arr = get_path(data, value_path)
        q_arr = get_path(data, quality_path) if quality_path else None
        if not isinstance(t_arr, list) or not isinstance(v_arr, list):
            return rows
        n = min(len(t_arr), len(v_arr))
        for i in range(n):
            ts = parse_source_timestamp(t_arr[i], fmt)
            if ts is None:
                continue
            raw_v = v_arr[i]
            if raw_v is None or (missing is not None and raw_v == missing):
                value = None
            else:
                value = coerce_number(raw_v)
            quality = 0
            if q_arr is not None and i < len(q_arr):
                quality = _quality_flag(q_arr[i], quality_map)
            rows.append((ts, value, quality))
        return rows

    # row shape
    items = get_path(data, series.items_path)
    if not isinstance(items, list):
        return rows
    ts_path = mapping.timestamp
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = parse_source_timestamp(get_path(item, ts_path), fmt)
        if ts is None:
            continue
        raw_v = get_path(item, value_path)
        if raw_v is None or (missing is not None and raw_v == missing):
            value = None
        else:
            value = coerce_number(raw_v)
        quality = _quality_flag(get_path(item, quality_path) if quality_path else None,
                                quality_map)
        rows.append((ts, value, quality))
    return rows


def _quality_flag(raw, quality_map: dict[str, str] | None) -> int:
    if raw is None:
        return 0
    if quality_map:
        canonical = quality_map.get(str(raw))
        if canonical is not None:
            return coerce_quality(canonical)
    return coerce_quality(raw)


# ── Generic fixed-schema cache ────────────────────────────────────────────────

class GenericDatasetCache:
    """Fixed-schema Postgres cache namespaced per (dataset id, fingerprint).

    Tables are shared across datasets; rows are isolated by ``dataset_key`` so a
    config change (new fingerprint) or a new dataset never corrupts another.
    """

    def __init__(self, pool: AsyncConnectionPool, dataset_key: str) -> None:
        self.pool = pool
        self.dataset_key = dataset_key

    @staticmethod
    async def ensure_tables(cur) -> None:
        await cur.execute(
            "CREATE TABLE IF NOT EXISTS cache_series ("
            "dataset_key TEXT NOT NULL, "
            "entity_id TEXT NOT NULL, "
            "measure_id TEXT NOT NULL, "
            "ts TIMESTAMPTZ NOT NULL, "
            "value DOUBLE PRECISION NOT NULL, "
            "quality SMALLINT NOT NULL DEFAULT 0, "
            "PRIMARY KEY (dataset_key, entity_id, measure_id, ts))")
        await cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_cache_series_range "
            "ON cache_series (dataset_key, entity_id, measure_id, ts)")
        await cur.execute(
            "CREATE TABLE IF NOT EXISTS cache_coverage ("
            "dataset_key TEXT NOT NULL, "
            "entity_id TEXT NOT NULL, "
            "measure_id TEXT NOT NULL, "
            "t_min TIMESTAMPTZ NOT NULL, "
            "t_max TIMESTAMPTZ NOT NULL, "
            "status TEXT NOT NULL CHECK (status IN ('queried', 'empty')), "
            "UNIQUE (dataset_key, entity_id, measure_id, t_min, t_max, status))")
        await cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_cache_coverage_range "
            "ON cache_coverage (dataset_key, entity_id, measure_id, t_min, t_max)")

    async def coverage(
        self, entity_id: str, measure_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Ranges already handled for this entity/measure: both 'queried'
        (may contain internal gaps) and 'empty' (known no-sample) intervals
        suppress further source probes, surviving restarts via the DB."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT t_min, t_max FROM cache_coverage "
                "WHERE dataset_key=%s AND entity_id=%s AND measure_id=%s "
                "AND t_max > %s AND t_min < %s",
                (self.dataset_key, entity_id, measure_id, start, end))
            rows = await cur.fetchall()
        return [(a, b) for a, b in rows]

    async def store(
        self,
        entity_id: str,
        measure_id: str,
        rows: list[tuple[datetime, float, int]],
        intervals: list[tuple[datetime, datetime, str]],
    ) -> None:
        """Conflict-safe store of fetched rows plus coverage intervals."""
        if not rows and not intervals:
            return
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cur = conn.cursor()
                if rows:
                    buf = io.StringIO()
                    for ts, value, quality in rows:
                        buf.write(f"{ts.isoformat()},{value},{quality}\n")
                    buf.seek(0)
                    await cur.execute("CREATE TEMP TABLE _cache_stage ("
                                      "ts TIMESTAMPTZ NOT NULL, "
                                      "value DOUBLE PRECISION NOT NULL, "
                                      "quality SMALLINT NOT NULL) "
                                      "ON COMMIT DROP")
                    async with cur.copy(
                            "COPY _cache_stage (ts, value, quality) "
                            "FROM STDIN WITH (FORMAT csv)") as copy_ctx:
                        while chunk := buf.read(1 << 20):
                            await copy_ctx.write(chunk.encode())
                    await cur.execute(
                        "INSERT INTO cache_series "
                        "(dataset_key, entity_id, measure_id, ts, value, quality) "
                        "SELECT %s, %s, %s, ts, value, quality FROM _cache_stage "
                        "ON CONFLICT (dataset_key, entity_id, measure_id, ts) "
                        "DO NOTHING",
                        (self.dataset_key, entity_id, measure_id))
                if intervals:
                    await cur.executemany(
                        "INSERT INTO cache_coverage "
                        "(dataset_key, entity_id, measure_id, t_min, t_max, status) "
                        "VALUES (%s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (dataset_key, entity_id, measure_id, "
                        "t_min, t_max, status) DO NOTHING",
                        [(self.dataset_key, entity_id, measure_id,
                          a, b, status) for a, b, status in intervals])

    async def read(
        self, entity_id: str, measure_id: str, start: datetime, end: datetime,
        cap: int,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, bool]:
        """Read cached rows in [start, end). Returns (t, value, quality, total,
        truncated). Missing samples are simply absent; never filled."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT extract(epoch FROM ts)::float8, value, quality, total "
                "FROM (SELECT ts, value, quality, "
                "COUNT(*) OVER () AS total "
                "FROM cache_series WHERE dataset_key=%s AND entity_id=%s "
                "AND measure_id=%s AND ts>=%s AND ts<%s) q "
                "ORDER BY ts LIMIT %s",
                (self.dataset_key, entity_id, measure_id, start, end, cap))
            rows = await cur.fetchall()
        if not rows:
            return (np.array([], dtype=float), np.array([], dtype=float),
                    np.array([], dtype=np.int16), 0, False)
        total = int(rows[0][3])
        t = np.array([r[0] for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
        q = np.array([r[2] for r in rows], dtype=np.int16)
        return t, v, q, total, len(rows) < total

    async def drop_dataset(self) -> None:
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cur = conn.cursor()
                await cur.execute("DELETE FROM cache_series WHERE dataset_key=%s",
                                  (self.dataset_key,))
                await cur.execute("DELETE FROM cache_coverage WHERE dataset_key=%s",
                                  (self.dataset_key,))

    @staticmethod
    async def clear_all(cur) -> None:
        await cur.execute("TRUNCATE cache_series, cache_coverage CASCADE")


def subtract_probed(a: datetime, b: datetime,
                    probed: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Split [a, b) around intervals already known to contain no samples."""
    pts = sorted(set([a, b] + [
        p for x, y in probed if y > a and x < b for p in (x, y) if a <= p <= b]))
    gaps = []
    for lo, hi in zip(pts, pts[1:]):
        if not any(x <= lo and hi <= y for x, y in probed):
            gaps.append((lo, hi))
    return gaps


# ── Config-driven adapter ─────────────────────────────────────────────────────

class ConfigurableHttpAdapter(DatasetAdapter):
    """Implements the canonical adapter contract against an arbitrary HTTP API
    described by a validated ``DatasetConfig``."""

    def __init__(
        self,
        config: DatasetConfig,
        cache: GenericDatasetCache,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        source: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.config = config
        self.cache = cache
        self.dataset_key = cache.dataset_key
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._entity_labels: dict[str, str] | None = None
        self._extent_cache: TimeExtent | None = None
        self._extent_at: datetime | None = None
        self._extent_ttl_s = 300.0
        self._probed_empty: dict[str, list[tuple[datetime, datetime]]] = {}

    @property
    def dataset_id(self) -> str:
        return self.config.dataset.id

    def _namespace(self, raw: Any) -> str:
        return f"{self.dataset_id}:{raw}"

    def _denamespace(self, entity_id: str) -> str:
        prefix = f"{self.dataset_id}:"
        if not entity_id.startswith(prefix):
            raise ValueError(
                f"unknown {self.dataset_id} entity ID {entity_id!r} "
                f"(expected prefix {prefix!r})")
        return entity_id[len(prefix):]

    def _client_for(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.source.base_url,
                timeout=self.config.source.timeout_seconds,
                headers=self.config.source.headers or None,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── describe ──────────────────────────────────────────────────────────────

    async def describe(self) -> DatasetSchema:
        measures = [
            MeasureSchema(
                id=measure_id,
                label=spec.label or measure_id,
                type=spec.type,
                unit=spec.unit,
            )
            for measure_id, spec in self.config.mapping.measures.items()
        ]
        return DatasetSchema(
            id=self.config.dataset.id,
            label=self.config.dataset.label,
            description=self.config.dataset.description,
            time={"id": "time", "label": "Time",
                  "timezone": self.config.mapping.timezone},
            entity={"id": "entity", "label": "Entity"},
            measures=measures,
            dimensions=[],
            capabilities=list(self.config.capabilities),
        )

    # ── entities ──────────────────────────────────────────────────────────────

    async def entities(
        self, search: str | None = None, offset: int = 0, limit: int = 100
    ) -> EntityPage:
        client = self._client_for()
        endpoints = self.config.endpoints.entities
        resp = await client.get(endpoints.path, params={})
        resp.raise_for_status()
        data = resp.json()
        items = get_path(data, endpoints.items_path)
        if not isinstance(items, list):
            items = []
        items = items[:_ENTITY_FETCH_CAP]

        eid_path = self.config.mapping.entity_id
        label_path = self.config.mapping.entity_label
        records: list[EntityRecord] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = get_path(item, eid_path)
            if raw is None:
                continue
            opaque = self._namespace(raw)
            if opaque in seen:
                continue
            seen.add(opaque)
            label = get_path(item, label_path) if label_path else None
            records.append(EntityRecord(
                id=opaque,
                label=str(label) if label is not None else str(raw),
                attributes={k: v for k, v in item.items()
                            if k not in (eid_path, label_path)},
            ))
        records.sort(key=lambda r: r.label.lower())

        if search:
            needle = search.lower()
            records = [r for r in records if needle in r.label.lower()
                       or needle in r.id.lower()]
        total = len(records)
        page = records[offset:offset + limit]
        self._entity_labels = {r.id: r.label for r in records}
        return EntityPage(items=page, total=total, offset=offset, limit=limit)

    # ── extent ────────────────────────────────────────────────────────────────

    async def extent(self) -> TimeExtent:
        now = datetime.now(timezone.utc)
        if (self._extent_cache is not None and self._extent_at is not None
                and (now - self._extent_at).total_seconds() < self._extent_ttl_s):
            return self._extent_cache
        client = self._client_for()
        endpoints = self.config.endpoints.extent
        resp = await client.get(endpoints.path)
        resp.raise_for_status()
        data = resp.json()
        start = get_path(data, endpoints.start_path)
        end = get_path(data, endpoints.end_path)
        if start is None or end is None:
            raise ValueError(
                f"extent endpoint {endpoints.path} did not expose "
                f"start_path={endpoints.start_path!r} / end_path={endpoints.end_path!r}")
        result = TimeExtent(start=float(start), end=float(end))
        self._extent_cache = result
        self._extent_at = now
        return result

    # ── HTTP series fetching (paged) ──────────────────────────────────────────

    async def _fetch_series(
        self, raw_entity: str, measure_id: str, start: datetime, end: datetime
    ) -> tuple[list[tuple[datetime, float, int]], bool]:
        """Fetch a range from the source, following offset pagination.
        Returns (rows, truncated) where truncated means the row cap was hit."""
        client = self._client_for()
        series = self.config.endpoints.series
        pagination = self.config.pagination
        fmt = self.config.mapping.timestamp_format
        params = {
            series.parameters["entity"]: raw_entity,
            series.parameters["start"]: format_request_timestamp(start, fmt),
            series.parameters["end"]: format_request_timestamp(end, fmt),
        }
        offset = 0
        rows: list[tuple[datetime, float, int]] = []
        truncated = False
        for _ in range(pagination.max_pages):
            page_params = dict(params)
            page_params[series.offset_param] = offset
            page_params[series.limit_param] = pagination.page_size
            resp = await client.get(series.path, params=page_params)
            if resp.status_code == 404:
                return rows, truncated
            resp.raise_for_status()
            data = resp.json()
            page = extract_rows(data, self.config, measure_id)
            for ts, value, quality in page:
                if value is None:
                    continue  # upstream missing sentinel: absent, not zero
                rows.append((ts, value, quality))
            next_offset = get_path(data, series.next_offset_path) \
                if series.next_offset_path else None
            if next_offset is None:
                if len(page) < pagination.page_size:
                    break
                next_offset = offset + len(page)
            if next_offset <= offset:
                break
            offset = int(next_offset)
            if len(rows) >= self.config.row_cap:
                rows = rows[:self.config.row_cap]
                truncated = True
                break
        return rows, truncated

    async def _hydrate(
        self, entity_id: str, measure_id: str, start: datetime, end: datetime
    ) -> tuple[bool, float]:
        """Fetch uncovered ranges into the cache. Returns (ok, source_ms)."""
        raw = self._denamespace(entity_id)
        t0 = time.perf_counter()
        covered = await self.cache.coverage(entity_id, measure_id, start, end)
        gaps = coverage_gaps(start, end, covered)
        probed = self._probed_empty.get(entity_id, [])
        if probed:
            gaps = [g for gap in gaps
                    for g in subtract_probed(*gap, probed)]
        if not gaps:
            return True, 0.0
        source_ms = 0.0
        for a, b in gaps:
            fetched_start = time.perf_counter()
            rows, truncated = await self._fetch_series(raw, measure_id, a, b)
            source_ms += (time.perf_counter() - fetched_start) * 1000
            if not rows:
                intervals = [(a, b, "empty")]
                if truncated:
                    # cap hit with zero rows: keep probing on later queries
                    intervals = []
                else:
                    self._probed_empty.setdefault(entity_id, []).append((a, b))
            else:
                t_min = rows[0][0]
                t_max = rows[-1][0]
                intervals: list[tuple[datetime, datetime, str]] = []
                if truncated:
                    intervals.append((a, t_max, "queried"))
                else:
                    intervals.append((a, b, "queried"))
                    if t_min > a:
                        intervals.append((a, t_min, "empty"))
                    if t_max < b:
                        intervals.append((t_max, b, "empty"))
            await self.cache.store(entity_id, measure_id, rows, intervals)
        return True, source_ms

    # ── query ─────────────────────────────────────────────────────────────────

    async def query(self, request: SeriesQuery) -> SeriesQueryResponse:
        query_start = time.perf_counter()
        self._validate_query(request)
        start = parse_timestamp(request.range.start)
        end = parse_timestamp(request.range.end)
        if end <= start:
            raise ValueError("range end must be after start")
        budget = request.resolution.pixel_width * request.resolution.points_per_pixel
        algo = request.downsampling

        series: list[CanonicalSeries] = []
        for entity_id in request.entity_ids:
            for measure_id in request.measure_ids:
                ok, source_ms = await self._hydrate(
                    entity_id, measure_id, start, end)
                if not ok:
                    series.append(CanonicalSeries(
                        entity_id=entity_id, measure_id=measure_id,
                        error="cache hydration failed"))
                    continue
                t, v, q, total, truncated = await self.cache.read(
                    entity_id, measure_id, start, end, self.config.row_cap)
                spec = self.config.mapping.measures[measure_id]
                payload = {
                    "t": t,
                    "avg": v,
                    "sample_count": np.ones(len(t), dtype=np.int64),
                    "quality_flag": q,
                }
                if len(t) == 0:
                    series.append(self._empty_series(
                        entity_id, measure_id, spec, start, end, source_ms,
                        query_start, truncated))
                    continue
                expected_step = self.config.expected_step_seconds
                gaps = find_gap_intervals(t, expected_step)
                quality_summary = {
                    str(int(flag)): int(np.count_nonzero(q == flag))
                    for flag in np.unique(q)
                }
                if expected_step is None and len(t) > 1:
                    expected_step = float(np.median(np.diff(t)))
                sampled, ds_ms = downsample(payload, algo, budget)
                out_t = np.round(sampled["t"], 6).tolist()
                points = SeriesPoints(
                    t=out_t,
                    value=np.round(sampled["avg"], 6).tolist(),
                    quality=[int(x) for x in sampled["quality_flag"]],
                    sample_count=[int(x) for x in sampled["sample_count"]],
                )
                series.append(CanonicalSeries(
                    entity_id=entity_id,
                    entity_label=self._label_for(entity_id),
                    measure_id=measure_id,
                    unit=spec.unit,
                    points=points,
                    resolution="raw",
                    expected_step_seconds=expected_step,
                    gaps=gaps,
                    quality_summary=quality_summary,
                    fidelity={
                        "source": "config_http_cache",
                        "interpolated": False,
                        "missing_intervals_preserved": True,
                        "truncated": truncated,
                        "cache_namespace": self.dataset_key,
                    },
                    metrics=QueryMetrics(
                        rows_source=total,
                        rows_scanned=len(t),
                        rows_returned=len(out_t),
                        query_ms=round((time.perf_counter() - query_start) * 1000, 1),
                        source_ms=round(source_ms, 1),
                        downsample_ms=round(ds_ms, 2),
                        truncated=truncated,
                    ),
                ))
        return SeriesQueryResponse(
            query=request,
            series=series,
            provenance=QueryProvenance(
                dataset_id=self.dataset_id,
                adapter="configurable",
                downsampling=algo,
                generated_at=datetime.now(timezone.utc).isoformat(),
            ),
        )

    def _validate_query(self, request: SeriesQuery) -> None:
        for measure_id in request.measure_ids:
            if measure_id not in self.config.mapping.measures:
                raise ValueError(
                    f"dataset {self.dataset_id} has no '{measure_id}' measure "
                    f"(available: {sorted(self.config.mapping.measures)})")
        if request.filters:
            raise ValueError(
                "this dataset has no filterable dimensions")

    def _empty_series(self, entity_id, measure_id, spec, start, end,
                      source_ms, query_start, truncated) -> CanonicalSeries:
        return CanonicalSeries(
            entity_id=entity_id,
            entity_label=self._label_for(entity_id),
            measure_id=measure_id,
            unit=spec.unit,
            resolution="raw",
            gaps=[],
            fidelity={
                "source": "config_http_cache",
                "interpolated": False,
                "missing_intervals_preserved": True,
                "truncated": truncated,
                "cache_namespace": self.dataset_key,
            },
            metrics=QueryMetrics(
                rows_source=0,
                rows_scanned=0,
                rows_returned=0,
                query_ms=round((time.perf_counter() - query_start) * 1000, 1),
                source_ms=round(source_ms, 1),
                truncated=truncated,
            ),
        )

    def _label_for(self, entity_id: str) -> str | None:
        if self._entity_labels is None:
            return None
        return self._entity_labels.get(entity_id)

    # ── matrix ────────────────────────────────────────────────────────────────

    async def matrix(self, request: MatrixQuery) -> MatrixResult:
        if request.measure_id not in self.config.mapping.measures:
            raise ValueError(
                f"dataset {self.dataset_id} has no '{request.measure_id}' measure")
        start = parse_timestamp(request.range.start)
        end = parse_timestamp(request.range.end)
        span = (end - start).total_seconds()
        if span <= 0:
            raise ValueError("range end must be after start")
        if not 10 <= request.pixel_width <= 2000:
            raise ValueError("pixel_width must be between 10 and 2000")

        ids = request.entity_ids[:200]
        for entity_id in ids:
            await self._hydrate(entity_id, request.measure_id, start, end)

        t_start = start.timestamp()
        t_end = end.timestamp()
        edges = np.linspace(t_start, t_end, request.pixel_width + 1)
        centres = (edges[:-1] + edges[1:]) / 2.0

        scores_grid: list[list[float | None]] = []
        for entity_id in ids:
            t, v, _, _, _ = await self.cache.read(
                entity_id, request.measure_id, start, end,
                self.config.row_cap)
            if len(t) == 0:
                scores_grid.append([None] * request.pixel_width)
                continue
            bucket_avg = bin_values(t, v, edges)
            z = rolling_zscore(bucket_avg, _ROLLING_WINDOW)
            normed = (np.clip(z, -3.0, 3.0) + 3.0) / 6.0
            scores_grid.append([
                round(float(value), 4) if np.isfinite(value) else None
                for value in normed
            ])
        return MatrixResult(
            dataset_id=self.dataset_id,
            transform=request.transform,
            t=centres.tolist(),
            entity_ids=ids,
            values=scores_grid,
            value_semantics="temporal_rolling_zscore",
            fidelity={
                "forward_filled": False,
                "missing_bins_preserved": True,
                "cache_namespace": self.dataset_key,
            },
            provenance={
                "adapter": "configurable",
                "source": "config_http_cache",
                "cache_namespace": self.dataset_key,
            },
        )
