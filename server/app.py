"""FastAPI composition: dataset registry, canonical routes, config management,
and legacy compatibility facades.

The project is dataset-independent: every registered ``config.json`` yields a
``ConfigurableHttpAdapter`` that hydrates a fixed-schema cache namespace.
Nothing is preloaded — the user registers their own API + config.json through
``POST /api/configs`` (or a config dropped into ``DCSVIZ_CONFIG_DIR``). The
only HLT-specific code lives in the datasource container, never here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg_pool import AsyncConnectionPool

from .api.common import drain_background, spawn_background
from .api.config import DatasetConfig, load_configs_from_dir, parse_config
from .api.configurable import ConfigurableHttpAdapter, GenericDatasetCache
from .api.contracts import (
    DatasetAdapter,
    DatasetSchema,
    DatasetSummary,
    EntityPage,
    MatrixQuery,
    MatrixResult,
    SeriesQuery,
    SeriesQueryResponse,
    TimeExtent as CanonicalTimeExtent,
)
from .config import Settings

settings = Settings.from_env()

pool: AsyncConnectionPool | None = None
dataset_adapters: dict[str, ConfigurableHttpAdapter] = {}
_registered_configs: dict[str, DatasetConfig] = {}

# Legacy route helpers (facades over the default adapter)
def _require_pool() -> AsyncConnectionPool:
    if pool is None:
        raise HTTPException(503, "database pool is not ready")
    return pool


def _get_dataset_adapter(dataset_id: str | None = None) -> DatasetAdapter:
    if not dataset_adapters:
        raise HTTPException(503, "dataset adapters are not ready")
    if dataset_id is None:
        return next(iter(dataset_adapters.values()))
    adapter = dataset_adapters.get(dataset_id)
    if adapter is None:
        raise HTTPException(404, f"unknown dataset: {dataset_id}")
    return adapter


def _default_adapter() -> ConfigurableHttpAdapter:
    if not dataset_adapters:
        raise HTTPException(503, "no datasets are configured")
    return next(iter(dataset_adapters.values()))


def _register(config: DatasetConfig) -> ConfigurableHttpAdapter:
    """Build (or replace) the adapter + fresh cache namespace for a config."""
    existing = dataset_adapters.get(config.dataset.id)
    if existing is not None:
        # New fingerprint => fresh namespace; the old namespace is dropped so
        # a changed schema can never leak stale rows into the new one.
        spawn_background(existing.cache.drop_dataset())
        existing_old = _registered_configs.get(config.dataset.id)
        dataset_adapters.pop(config.dataset.id, None)
        if existing_old is not None:
            del _registered_configs[config.dataset.id]
    dataset_key = f"{config.dataset.id}:{config.fingerprint()}"
    cache = GenericDatasetCache(_require_pool(), dataset_key)
    adapter = ConfigurableHttpAdapter(config, cache)
    dataset_adapters[config.dataset.id] = adapter
    _registered_configs[config.dataset.id] = config
    return adapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = AsyncConnectionPool(
        settings.db_url, min_size=1, max_size=8, open=False)
    await pool.open()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await GenericDatasetCache.ensure_tables(cur)
    for config in load_configs_from_dir(settings.config_dir):
        try:
            _register(config)
            print(f"  dataset {config.dataset.id!r} registered "
                  f"({config.dataset.label})")
        except Exception as exc:
            print(f"  dataset {config.dataset.id!r} failed to register: {exc}")
    yield
    await drain_background()
    for adapter in dataset_adapters.values():
        await adapter.aclose()
    await pool.close()


app = FastAPI(title="anaviz", lifespan=lifespan)


class ConfigRegistration(BaseModel):
    """POST /api/configs body: a raw config.json object (env refs allowed)."""
    config: dict = Field(..., description="the full config.json payload")


class ConfigSummary(BaseModel):
    id: str
    label: str
    description: str | None = None
    fingerprint: str
    capabilities: list[str]


# ── Dataset discovery and configuration registry ─────────────────────────────

@app.get("/api/datasets", response_model=list[DatasetSummary])
async def datasets():
    """Discover datasets without exposing source or database-specific fields."""
    summaries = []
    for adapter in dataset_adapters.values():
        schema = await adapter.describe()
        summaries.append(DatasetSummary(
            id=schema.id,
            label=schema.label,
            description=schema.description,
            capabilities=schema.capabilities,
        ))
    return summaries


@app.get("/api/configs", response_model=list[ConfigSummary])
async def configs():
    """List registered configs (never raw header/secret values)."""
    return [
        ConfigSummary(
            id=cfg.dataset.id,
            label=cfg.dataset.label,
            description=cfg.dataset.description,
            fingerprint=cfg.fingerprint(),
            capabilities=list(cfg.capabilities),
        )
        for cfg in _registered_configs.values()
    ]


@app.post("/api/configs", response_model=ConfigSummary, status_code=201)
async def register_config(registration: ConfigRegistration):
    """Validate and register a new dataset from a config.json payload."""
    try:
        config = parse_config(registration.config)
    except ValueError as exc:
        raise HTTPException(400, f"invalid configuration: {exc}") from exc
    adapter = _register(config)
    await adapter.describe()   # fail fast on an unusable source contract
    return ConfigSummary(
        id=config.dataset.id,
        label=config.dataset.label,
        description=config.dataset.description,
        fingerprint=config.fingerprint(),
        capabilities=list(config.capabilities),
    )


@app.delete("/api/configs/{dataset_id}", status_code=204)
async def unregister_config(dataset_id: str):
    """Remove a dataset and drop its cache namespace."""
    adapter = dataset_adapters.get(dataset_id)
    if adapter is None:
        raise HTTPException(404, f"unknown dataset: {dataset_id}")
    await adapter.cache.drop_dataset()
    dataset_adapters.pop(dataset_id, None)
    _registered_configs.pop(dataset_id, None)
    await adapter.aclose()


@app.get("/api/datasets/{dataset_id}/schema", response_model=DatasetSchema)
async def dataset_schema(dataset_id: str):
    return await _get_dataset_adapter(dataset_id).describe()


@app.get("/api/datasets/{dataset_id}/entities", response_model=EntityPage)
async def dataset_entities(
    dataset_id: str,
    search: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=10000),
):
    return await _get_dataset_adapter(dataset_id).entities(search, offset, limit)


@app.get(
    "/api/datasets/{dataset_id}/extent", response_model=CanonicalTimeExtent)
async def dataset_extent(dataset_id: str):
    return await _get_dataset_adapter(dataset_id).extent()


@app.post("/api/query", response_model=SeriesQueryResponse)
async def canonical_query(request: SeriesQuery):
    try:
        return await _get_dataset_adapter(request.dataset_id).query(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/matrix", response_model=MatrixResult)
async def canonical_matrix_query(request: MatrixQuery):
    try:
        return await _get_dataset_adapter(request.dataset_id).matrix(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ── Legacy compatibility facades (over the default adapter) ──────────────────

@app.get("/api/extent")
async def extent():
    result = await _default_adapter().extent()
    return {"t_min": result.start, "t_max": result.end}


@app.get("/api/channels")
async def channels():
    page = await _default_adapter().entities(limit=10_000)
    return [{"element_id": item.id, "name": item.label} for item in page.items]


@app.get("/api/series")
async def series(
    t0: str,
    t1: str,
    element_id: list[str] = Query(min_length=1, max_length=64),
    px: int = Query(default=1000, ge=50, le=8000),
    k: int = Query(default=2, ge=1, le=4),
    algo: str = Query(default="M4", pattern="^(LTTB|M4|MINMAXLTTB|RAW)$"),
):
    adapter = _default_adapter()
    schema = await adapter.describe()
    measure_id = schema.measures[0].id if schema.measures else "value"
    request = SeriesQuery(
        dataset_id=adapter.dataset_id,
        entity_ids=[f"{adapter.dataset_id}:{eid}" for eid in element_id],
        measure_ids=[measure_id],
        range={"start": t0, "end": t1},
        resolution={"strategy": "auto", "pixel_width": px,
                    "points_per_pixel": k},
        downsampling=algo,
    )
    try:
        response = await adapter.query(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    results = []
    for item in response.series:
        metrics = item.metrics
        results.append({
            "element_id": item.entity_id,
            "resolution": item.resolution,
            "algo": response.provenance.downsampling,
            "rows_raw": metrics.rows_source,
            "rows_scanned": metrics.rows_scanned,
            "rows_returned": metrics.rows_returned,
            "truncated": metrics.truncated,
            "expected_step_seconds": item.expected_step_seconds,
            "gap_intervals": item.gaps,
            "gap_count": len(item.gaps),
            "quality_summary": item.quality_summary,
            "data_fidelity": item.fidelity,
            "query_ms": metrics.query_ms,
            "archive_ms": metrics.source_ms,
            "ds_ms": metrics.downsample_ms,
            "series": {
                "t": item.points.t,
                "avg": item.points.value,
                "quality_flag": item.points.quality,
                "sample_count": item.points.sample_count,
            },
        })
    return results


@app.get("/api/heatmap")
async def heatmap_endpoint(
    t0: str,
    t1: str,
    px: int = Query(default=200, ge=10, le=2000),
    channel_ids: str | None = None,
):
    adapter = _default_adapter()
    if channel_ids:
        ids = [f"{adapter.dataset_id}:{cid.strip()}"
               for cid in channel_ids.split(",") if cid.strip()]
    else:
        page = await adapter.entities(limit=200)
        ids = [item.id for item in page.items]
    try:
        result = await adapter.matrix(MatrixQuery(
            dataset_id=adapter.dataset_id,
            entity_ids=ids,
            measure_id="value",
            range={"start": t0, "end": t1},
            pixel_width=px,
        ))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "t": result.t,
        "channels": result.entity_ids,
        "scores": result.values,
        "score_kind": result.value_semantics,
        "missing_value": None,
        "data_fidelity": result.fidelity,
    }


@app.get("/api/status")
async def status():
    return {"refreshing": []}


@app.get("/api/clear-cache")
async def clear_cache():
    await drain_background()
    async with _require_pool().connection() as conn:
        async with conn.cursor() as cur:
            await GenericDatasetCache.clear_all(cur)
    return {"ok": True}


# ── Static frontend ───────────────────────────────────────────────────────────

app.mount(
    "/static", StaticFiles(directory=str(settings.frontend_path)),
    name="frontend")


@app.get("/")
async def index():
    return FileResponse(settings.frontend_path / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)
