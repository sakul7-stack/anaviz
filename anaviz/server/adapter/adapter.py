"""Config-driven HTTP adapter: fetches from source, hydrates cache."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .cache import GenericCache
from .common import coverage_gaps, subtract_probed
from ..api.config import DatasetConfig
from ..api.contracts import (
    DatasetSchema, EntityPage, EntityRecord, MeasureSchema, TimeExtent,
)

_ENTITY_FETCH_CAP = 100_000


def _get_path(obj: dict, path: str | None) -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _format_ts(dt: datetime, fmt: str) -> Any:
    if fmt == "epoch_ms":
        return int(dt.timestamp() * 1000)
    if fmt == "iso":
        return dt.isoformat()
    return dt.timestamp()


class ConfigurableAdapter:
    """Fetches data from an HTTP source and serves it through a cache."""

    def __init__(self, config: DatasetConfig, cache: GenericCache, *, transport=None):
        self.config = config
        self.cache = cache
        self.dataset_key = cache.dataset_key
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._labels: dict[str, str] = {}
        self._extent_cache: TimeExtent | None = None
        self._extent_at: datetime | None = None
        self._probed: dict[str, list[tuple[datetime, datetime]]] = {}
        self._inferred_step: float | None = None

    @property
    def dataset_id(self) -> str:
        return self.config.dataset.id

    def _ns(self, raw: Any) -> str:
        return f"{self.dataset_id}:{raw}"

    def _unnamed(self, entity_id: str) -> str:
        prefix = f"{self.dataset_id}:"
        if not entity_id.startswith(prefix):
            raise ValueError(f"bad entity ID: {entity_id!r}")
        return entity_id[len(prefix):]

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.source.base_url,
                timeout=self.config.source.timeout_seconds,
                headers=self.config.source.headers or None,
                transport=self._transport,
            )
        return self._client

    async def aclose(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # describe
    async def describe(self) -> DatasetSchema:
        measures = [
            MeasureSchema(id=mid, label=s.label or mid, type=s.type, unit=s.unit)
            for mid, s in self.config.mapping.measures.items()
        ]
        return DatasetSchema(
            id=self.config.dataset.id, label=self.config.dataset.label,
            description=self.config.dataset.description,
            measures=measures, capabilities=list(self.config.capabilities))

    # entities
    async def entities(self, search=None, offset=0, limit=100) -> EntityPage:
        resp = await self._http().get(self.config.endpoints.entities.path)
        resp.raise_for_status()
        items = _get_path(resp.json(), self.config.endpoints.entities.items_path)
        if not isinstance(items, list):
            items = []
        items = items[:_ENTITY_FETCH_CAP]
        eid_path = self.config.mapping.entity_id
        lbl_path = self.config.mapping.entity_label
        records, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = _get_path(item, eid_path)
            if raw is None:
                continue
            opaque = self._ns(raw)
            if opaque in seen:
                continue
            seen.add(opaque)
            label = _get_path(item, lbl_path) if lbl_path else None
            records.append(EntityRecord(
                id=opaque, label=str(label) if label is not None else str(raw),
                attributes={k: v for k, v in item.items() if k not in (eid_path, lbl_path)}))
        records.sort(key=lambda r: r.label.lower())
        if search:
            needle = search.lower()
            records = [r for r in records if needle in r.label.lower() or needle in r.id.lower()]
        self._labels = {r.id: r.label for r in records}
        return EntityPage(items=records[offset:offset+limit],
                          total=len(records), offset=offset, limit=limit)

    # extent
    async def extent(self) -> TimeExtent:
        now = datetime.now(timezone.utc)
        if (self._extent_cache and self._extent_at
                and (now - self._extent_at).total_seconds() < 300):
            return self._extent_cache
        ep = self.config.endpoints.extent
        data = (await self._http().get(ep.path)).json()
        start, end = _get_path(data, ep.start_path), _get_path(data, ep.end_path)
        if start is None or end is None:
            raise ValueError("extent endpoint missing start/end values")
        self._extent_cache = TimeExtent(start=float(start), end=float(end))
        self._extent_at = now
        return self._extent_cache

    # fetch series (paged HTTP)
    async def _fetch(self, raw_entity, measure_id, start, end):
        from .query import extract_rows  # avoid circular
        cfg = self.config
        series, pag = cfg.endpoints.series, cfg.pagination
        fmt = cfg.mapping.timestamp_format
        base_params = {
            series.parameters["entity"]: raw_entity,
            series.parameters["start"]: _format_ts(start, fmt),
            series.parameters["end"]: _format_ts(end, fmt),
        }
        offset, rows, truncated = 0, [], False
        for _ in range(pag.max_pages):
            params = {**base_params, series.offset_param: offset,
                      series.limit_param: pag.page_size}
            resp = await self._http().get(series.path, params=params)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            page = extract_rows(resp.json(), cfg, measure_id)
            for ts, val, q in page:
                if val is not None:
                    rows.append((ts, val, q))
            nxt = _get_path(resp.json(), series.next_offset_path) \
                if series.next_offset_path else None
            if nxt is None:
                if len(page) < pag.page_size:
                    break
                nxt = offset + len(page)
            if nxt <= offset:
                break
            offset = int(nxt)
            if len(rows) >= cfg.row_cap:
                rows = rows[:cfg.row_cap]; truncated = True; break
        return rows, truncated

    def _rollup_levels(self):
        if not self.config.rollup_enabled or not self.config.rollup_levels:
            return None
        return list(self.config.rollup_levels)

    async def _hydrate(self, entity_id, measure_id, start, end):
        raw = self._unnamed(entity_id)
        t0 = time.perf_counter()
        covered = await self.cache.coverage(entity_id, measure_id, start, end)
        gaps = coverage_gaps(start, end, covered)
        probed = self._probed.get(entity_id, [])
        if probed:
            gaps = [g for gap in gaps for g in subtract_probed(*gap, probed)]
        if not gaps:
            return True, 0.0
        src_ms = 0.0
        for a, b in gaps:
            ft = time.perf_counter()
            rows, truncated = await self._fetch(raw, measure_id, a, b)
            src_ms += (time.perf_counter() - ft) * 1000
            levels = self._rollup_levels()
            if not rows:
                intervals = [] if truncated else [(a, b, "empty")]
                if not truncated:
                    self._probed.setdefault(entity_id, []).append((a, b))
            else:
                t_min, t_max = rows[0][0], rows[-1][0]
                if truncated:
                    intervals = [(a, t_max, "queried")]
                else:
                    intervals = [(a, b, "queried")]
                    if t_min > a:
                        intervals.append((a, t_min, "empty"))
                    if t_max < b:
                        intervals.append((t_max, b, "empty"))
            await self.cache.store(entity_id, measure_id, rows, intervals,
                                   rollup_levels=levels)
        return True, src_ms
