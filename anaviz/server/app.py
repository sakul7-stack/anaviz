"""FastAPI app: dataset registry, routes, config management."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg_pool import AsyncConnectionPool

from .adapter.common import drain_background, spawn_background
from .api.config import DatasetConfig, load_configs_from_dir, parse_config
from .adapter.adapter import ConfigurableAdapter
from .adapter.cache import GenericCache
from .adapter.query import run_query, run_matrix
from .api.contracts import (
    DatasetSchema, DatasetSummary, EntityPage, MatrixQuery, MatrixResult,
    SeriesQuery, SeriesQueryResponse, TimeExtent,
)
from .config import Settings

settings = Settings.from_env()
pool: AsyncConnectionPool | None = None
adapters: dict[str, ConfigurableAdapter] = {}
configs: dict[str, DatasetConfig] = {}


def _pool() -> AsyncConnectionPool:
    if pool is None:
        raise HTTPException(503, "database not ready")
    return pool


def _adapter(dataset_id: str | None = None) -> ConfigurableAdapter:
    if not adapters:
        raise HTTPException(503, "no datasets configured")
    if dataset_id is None:
        return next(iter(adapters.values()))
    a = adapters.get(dataset_id)
    if a is None:
        raise HTTPException(404, f"unknown dataset: {dataset_id}")
    return a


def _register(config: DatasetConfig) -> ConfigurableAdapter:
    existing = adapters.get(config.dataset.id)
    if existing:
        spawn_background(existing.cache.drop_dataset())
        adapters.pop(config.dataset.id, None)
        configs.pop(config.dataset.id, None)
    key = f"{config.dataset.id}:{config.fingerprint()}"
    cache = GenericCache(_pool(), key)
    adapter = ConfigurableAdapter(config, cache)
    adapters[config.dataset.id] = adapter
    configs[config.dataset.id] = config
    return adapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = AsyncConnectionPool(settings.db_url, min_size=1, max_size=8, open=False)
    await pool.open()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await GenericCache.ensure_tables(cur)
    for cfg in load_configs_from_dir(settings.config_dir):
        try:
            _register(cfg)
            print(f"  loaded: {cfg.dataset.id} ({cfg.dataset.label})")
        except Exception as exc:
            print(f"  failed: {cfg.dataset.id}: {exc}")
    yield
    await drain_background()
    for a in adapters.values():
        await a.aclose()
    await pool.close()


app = FastAPI(title="tsViz", lifespan=lifespan)


class ConfigBody(BaseModel):
    config: dict = Field(..., description="raw config.json payload")


class ConfigInfo(BaseModel):
    id: str; label: str; fingerprint: str
    description: str | None = None


# Dataset discovery

@app.get("/api/datasets", response_model=list[DatasetSummary])
async def list_datasets():
    return [DatasetSummary(id=(s := await a.describe()).id, label=s.label,
                           description=s.description, capabilities=s.capabilities)
            for a in adapters.values()]


@app.get("/api/configs", response_model=list[ConfigInfo])
async def list_configs():
    return [ConfigInfo(id=c.dataset.id, label=c.dataset.label,
                       description=c.dataset.description, fingerprint=c.fingerprint())
            for c in configs.values()]


@app.post("/api/configs", response_model=ConfigInfo, status_code=201)
async def register_config(body: ConfigBody):
    try:
        cfg = parse_config(body.config)
    except ValueError as exc:
        raise HTTPException(400, f"bad config: {exc}") from exc
    adapter = _register(cfg)
    await adapter.describe()
    return ConfigInfo(id=cfg.dataset.id, label=cfg.dataset.label,
                      description=cfg.dataset.description, fingerprint=cfg.fingerprint())


@app.delete("/api/configs/{dataset_id}", status_code=204)
async def remove_config(dataset_id: str):
    a = adapters.get(dataset_id)
    if a is None:
        raise HTTPException(404, f"unknown: {dataset_id}")
    await a.cache.drop_dataset()
    adapters.pop(dataset_id, None); configs.pop(dataset_id, None)
    await a.aclose()


@app.get("/api/datasets/{dataset_id}/schema", response_model=DatasetSchema)
async def schema(dataset_id: str):
    return await _adapter(dataset_id).describe()


@app.get("/api/datasets/{dataset_id}/entities", response_model=EntityPage)
async def entities(dataset_id: str, search: str | None = None,
                   offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=10000)):
    return await _adapter(dataset_id).entities(search, offset, limit)


@app.get("/api/datasets/{dataset_id}/extent", response_model=TimeExtent)
async def extent(dataset_id: str):
    return await _adapter(dataset_id).extent()


@app.post("/api/query", response_model=SeriesQueryResponse)
async def query(request: SeriesQuery):
    try:
        return await run_query(_adapter(request.dataset_id), request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/matrix", response_model=MatrixResult)
async def matrix(request: MatrixQuery):
    try:
        return await run_matrix(_adapter(request.dataset_id), request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# Legacy facades

@app.get("/api/extent")
async def legacy_extent():
    r = await _adapter().extent()
    return {"t_min": r.start, "t_max": r.end}


@app.get("/api/channels")
async def legacy_channels():
    page = await _adapter().entities(limit=10_000)
    return [{"element_id": item.id, "name": item.label} for item in page.items]


@app.get("/api/series")
async def legacy_series(
    t0: str, t1: str,
    element_id: list[str] = Query(min_length=1, max_length=64),
    px: int = Query(1000, ge=50, le=8000),
    k: int = Query(2, ge=1, le=4),
    algo: str = Query("M4", pattern="^(LTTB|M4|MINMAXLTTB|RAW)$"),
):
    a = _adapter()
    schema = await a.describe()
    mid = schema.measures[0].id if schema.measures else "value"
    req = SeriesQuery(
        dataset_id=a.dataset_id,
        entity_ids=[f"{a.dataset_id}:{eid}" for eid in element_id],
        measure_ids=[mid], range={"start": t0, "end": t1},
        resolution={"strategy": "auto", "pixel_width": px, "points_per_pixel": k},
        downsampling=algo)
    try:
        resp = await run_query(a, req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [{
        "element_id": s.entity_id, "resolution": s.resolution,
        "algo": resp.provenance.downsampling,
        "rows_raw": s.metrics.rows_source, "rows_scanned": s.metrics.rows_scanned,
        "rows_returned": s.metrics.rows_returned, "truncated": s.metrics.truncated,
        "expected_step_seconds": s.expected_step_seconds, "gap_intervals": s.gaps,
        "gap_count": len(s.gaps), "quality_summary": s.quality_summary,
        "data_fidelity": s.fidelity, "query_ms": s.metrics.query_ms,
        "archive_ms": s.metrics.source_ms, "ds_ms": s.metrics.downsample_ms,
        "series": {"t": s.points.t, "avg": s.points.value,
                   "quality_flag": s.points.quality, "sample_count": s.points.sample_count},
    } for s in resp.series]


@app.get("/api/heatmap")
async def legacy_heatmap(
    t0: str, t1: str, px: int = Query(200, ge=10, le=2000),
    channel_ids: str | None = None,
):
    a = _adapter()
    if channel_ids:
        ids = [f"{a.dataset_id}:{c.strip()}" for c in channel_ids.split(",") if c.strip()]
    else:
        page = await a.entities(limit=200)
        ids = [item.id for item in page.items]
    try:
        result = await run_matrix(a, MatrixQuery(
            dataset_id=a.dataset_id, entity_ids=ids,
            measure_id="value", range={"start": t0, "end": t1}, pixel_width=px))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"t": result.t, "channels": result.entity_ids,
            "scores": result.values, "score_kind": result.value_semantics,
            "missing_value": None, "data_fidelity": result.fidelity}


@app.get("/api/status")
async def status():
    return {"refreshing": []}


@app.get("/api/clear-cache")
async def clear_cache():
    await drain_background()
    async with _pool().connection() as conn:
        async with conn.cursor() as cur:
            await GenericCache.clear_all(cur)
    return {"ok": True}


# Static frontend

app.mount("/static", StaticFiles(directory=str(settings.frontend_path)), name="frontend")


@app.get("/")
async def index():
    return FileResponse(settings.frontend_path / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)
