"""Canonical contracts for dataset-independent time-series visualization.

Adapters translate source-specific identifiers and storage layouts into these
models. Renderers and analysis plugins consume only the canonical contracts.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


ScalarType = Literal["number", "integer", "string", "boolean", "category"]
EvidenceKind = Literal["score", "flag", "threshold", "interval", "annotation"]


class TimeAxisSchema(BaseModel):
    id: str = "time"
    label: str = "Time"
    timezone: str = "UTC"


class EntityAxisSchema(BaseModel):
    id: str = "entity"
    label: str = "Entity"


class MeasureSchema(BaseModel):
    id: str
    label: str
    type: ScalarType = "number"
    unit: str | None = None
    aggregations: list[str] = Field(default_factory=lambda: ["avg", "min", "max"])
    interpolation: Literal["none", "linear", "step"] = "none"


class DimensionSchema(BaseModel):
    id: str
    label: str
    type: ScalarType = "category"


class DatasetSchema(BaseModel):
    id: str
    label: str
    description: str | None = None
    schema_version: str = "1.0"
    time: TimeAxisSchema = Field(default_factory=TimeAxisSchema)
    entity: EntityAxisSchema = Field(default_factory=EntityAxisSchema)
    measures: list[MeasureSchema]
    dimensions: list[DimensionSchema] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class DatasetSummary(BaseModel):
    id: str
    label: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class EntityRecord(BaseModel):
    id: str
    label: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class EntityPage(BaseModel):
    items: list[EntityRecord]
    total: int
    offset: int = 0
    limit: int = 100


class TimeExtent(BaseModel):
    start: float | None
    end: float | None


class TimeRange(BaseModel):
    start: float | str
    end: float | str


class ResolutionRequest(BaseModel):
    strategy: Literal["auto"] = "auto"
    pixel_width: int = Field(default=1000, ge=50, le=8000)
    points_per_pixel: int = Field(default=2, ge=1, le=4)


class SeriesQuery(BaseModel):
    dataset_id: str = "default"
    entity_ids: list[str] = Field(min_length=1, max_length=64)
    measure_ids: list[str] = Field(default_factory=lambda: ["value"], min_length=1)
    range: TimeRange
    resolution: ResolutionRequest = Field(default_factory=ResolutionRequest)
    downsampling: Literal["LTTB", "M4", "MINMAXLTTB", "RAW"] = "M4"
    filters: dict[str, list[str]] = Field(default_factory=dict)


class SeriesPoints(BaseModel):
    t: list[float] = Field(default_factory=list)
    value: list[float | None] = Field(default_factory=list)
    min: list[float | None] | None = None
    max: list[float | None] | None = None
    first: list[float | None] | None = None
    last: list[float | None] | None = None
    min_t: list[float] | None = None
    max_t: list[float] | None = None
    first_t: list[float] | None = None
    last_t: list[float] | None = None
    quality: list[int | str | None] = Field(default_factory=list)
    sample_count: list[int | None] = Field(default_factory=list)


class QueryMetrics(BaseModel):
    rows_source: int = 0
    rows_scanned: int = 0
    rows_returned: int = 0
    query_ms: float = 0.0
    source_ms: float = 0.0
    downsample_ms: float = 0.0
    truncated: bool = False


class CanonicalSeries(BaseModel):
    entity_id: str
    entity_label: str | None = None
    measure_id: str
    unit: str | None = None
    points: SeriesPoints = Field(default_factory=SeriesPoints)
    resolution: str | None = None
    expected_step_seconds: float | None = None
    gaps: list[list[float]] = Field(default_factory=list)
    quality_summary: dict[str, int] = Field(default_factory=dict)
    fidelity: dict[str, Any] = Field(default_factory=dict)
    metrics: QueryMetrics = Field(default_factory=QueryMetrics)
    error: str | None = None


class QueryProvenance(BaseModel):
    dataset_id: str
    adapter: str
    downsampling: str
    generated_at: str
    contract_version: str = "1.0"


class SeriesQueryResponse(BaseModel):
    query: SeriesQuery
    series: list[CanonicalSeries]
    provenance: QueryProvenance




class EvidenceQuery(BaseModel):
    dataset_id: str = "default"
    plugin: str
    entity_ids: list[str] = Field(min_length=1, max_length=64)
    measure_id: str = "value"
    range: TimeRange
    parameters: dict[str, Any] = Field(default_factory=dict)


class MatrixQuery(BaseModel):
    dataset_id: str = "default"
    entity_ids: list[str] = Field(min_length=1, max_length=200)
    measure_id: str = "value"
    range: TimeRange
    transform: str = "temporal_rolling_zscore"
    pixel_width: int = Field(default=200, ge=10, le=2000)


class MatrixResult(BaseModel):
    dataset_id: str
    transform: str
    t: list[float] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    values: list[list[float | None]] = Field(default_factory=list)
    value_semantics: str
    missing_value: None = None
    fidelity: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
class EvidenceOutput(BaseModel):
    kind: EvidenceKind
    semantics: str
    unit: str | None = None
    entity_id: str | None = None
    measure_id: str | None = None
    t: list[float] = Field(default_factory=list)
    values: list[Any] = Field(default_factory=list)
    ground_truth: bool = False


class EvidenceResult(BaseModel):
    plugin: str
    version: str
    outputs: list[EvidenceOutput]
    parameters: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class DatasetAdapter(Protocol):
    """Source adapter consumed by generic API routes."""

    @property
    def dataset_id(self) -> str: ...

    async def describe(self) -> DatasetSchema: ...

    async def entities(
        self, search: str | None = None, offset: int = 0, limit: int = 100
    ) -> EntityPage: ...

    async def extent(self) -> TimeExtent: ...

    async def query(self, request: SeriesQuery) -> SeriesQueryResponse: ...

    async def matrix(self, request: MatrixQuery) -> MatrixResult: ...


@runtime_checkable
class QueryCache(Protocol):
    """Optional cache boundary; implementations may use any storage engine."""

    async def get(self, fingerprint: str) -> SeriesQueryResponse | None: ...

    async def put(self, fingerprint: str, value: SeriesQueryResponse) -> None: ...

    async def invalidate_dataset(self, dataset_id: str) -> None: ...
