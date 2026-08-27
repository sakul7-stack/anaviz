"""Dataset configuration: the validated ``config.json`` a user supplies.

A configuration describes an arbitrary HTTP time-series API in terms of the
canonical visualization model: dataset metadata, endpoint paths, JSON-path
field mappings, timestamp handling, pagination, and measure definitions.
Configurations are declarative data only — no code, SQL, or templates are
accepted — and are fingerprinted so a changed schema creates a new cache
namespace instead of corrupting an existing one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Sub-models

class DatasetInfo(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = "Time-series dataset"
    description: str | None = None


class HttpSource(BaseModel):
    type: Literal["http"] = "http"
    base_url: str
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _safe_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must be an http(s) URL")
        if "@" in value.split("://", 1)[1]:
            raise ValueError("base_url must not contain embedded credentials")
        return value.rstrip("/")


class EntitiesEndpoint(BaseModel):
    path: str = Field(pattern=r"^/")
    method: Literal["GET"] = "GET"
    # None → the response body itself is the entity list
    items_path: str | None = None


class ExtentEndpoint(BaseModel):
    path: str = Field(pattern=r"^/")
    method: Literal["GET"] = "GET"
    start_path: str = "start"
    end_path: str = "end"


class SeriesEndpoint(BaseModel):
    path: str = Field(pattern=r"^/")
    method: Literal["GET"] = "GET"
    # "row"      → items are objects, each carrying its own timestamp/value
    # "columnar" → the response carries parallel arrays (t + value + quality)
    shape: Literal["row", "columnar"] = "row"
    parameters: dict[str, str] = Field(
        default_factory=lambda: {
            "entity": "entity", "start": "start", "end": "end"})
    items_path: str | None = None      # row shape only
    next_offset_path: str | None = None
    offset_param: str = "offset"
    limit_param: str = "limit"

    @field_validator("parameters")
    @classmethod
    def _require_canonical_keys(cls, value: dict[str, str]) -> dict[str, str]:
        missing = {"entity", "start", "end"} - set(value)
        if missing:
            raise ValueError(
                f"series.parameters must map canonical keys "
                f"entity/start/end, missing: {sorted(missing)}")
        return value


class MeasureSpec(BaseModel):
    # Defaults to its mapping key, set by the _measure_keys_match_ids validator
    id: str = ""
    label: str | None = None
    # None → the value lives at the measure id path (row shape)
    value_path: str | None = None
    unit: str | None = None
    type: Literal["number", "integer", "string", "boolean", "category"] = "number"


class Mapping(BaseModel):
    entity_id: str = "id"
    entity_label: str | None = None
    timestamp: str = "ts"
    timestamp_format: Literal["unix", "epoch_ms", "iso"] = "unix"
    timezone: str = "UTC"
    measures: dict[str, MeasureSpec] = Field(min_length=1)
    quality: str | None = None
    quality_map: dict[str, str] = Field(default_factory=dict)
    # Upstream sentinel that means "missing sample" (e.g. -9999)
    missing_value: float | str | None = None

    @field_validator("measures")
    @classmethod
    def _measure_keys_match_ids(cls, value: dict[str, MeasureSpec]) -> dict[str, MeasureSpec]:
        for measure_id, spec in value.items():
            if spec.id and spec.id != measure_id:
                raise ValueError(
                    f"measure key {measure_id!r} must equal its id {spec.id!r}")
            spec.id = measure_id
        return value


class Pagination(BaseModel):
    type: Literal["offset", "none"] = "offset"
    page_size: int = Field(default=10_000, ge=1, le=1_000_000)
    max_pages: int = Field(default=20, ge=1, le=1000)


class Endpoints(BaseModel):
    entities: EntitiesEndpoint
    extent: ExtentEndpoint
    series: SeriesEndpoint


# Top-level configuration

DEFAULT_CAPABILITIES = [
    "entities", "extent", "series", "coverage", "quality",
    "sample_count", "downsampling", "matrix",
]


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    dataset: DatasetInfo
    source: HttpSource
    endpoints: Endpoints
    mapping: Mapping
    pagination: Pagination = Field(default_factory=Pagination)
    expected_step_seconds: float | None = None
    # On-demand rollup tiers are strictly opt-in: nothing is derived
    # automatically. Declaring rollup_levels (bucket sizes in seconds) opts a
    # dataset in; rollup_enabled is a master kill-switch (default true; set
    # false to force every range to raw rows even if levels are declared).
    rollup_enabled: bool = True
    rollup_levels: list[float] | None = None
    capabilities: list[str] = Field(default_factory=lambda: list(DEFAULT_CAPABILITIES))

    @field_validator("rollup_levels")
    @classmethod
    def _rollup_levels_positive(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if not value or any(v <= 0 for v in value):
            raise ValueError("rollup_levels must be positive bucket sizes in seconds")
        if len(value) > 12:
            raise ValueError("rollup_levels must have at most 12 buckets")
        return sorted(value)

    def fingerprint(self) -> str:
        """Stable hash of the resolved configuration (schema => namespace)."""
        canonical = json.dumps(
            json.loads(self.model_dump_json()), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# Env substitution and loading

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# Configs may only read DCSVIZ_* environment variables. Anything else would let
# an unauthenticated POST /api/configs exfiltrate server secrets (DATABASE_URL,
# API keys, ...) by embedding them in base_url/headers and pointing at an
# attacker-controlled host.
ENV_ALLOWLIST_PREFIX = "DCSVIZ_"


def resolve_env(value: Any) -> Any:
    """Expand ``${VAR}`` / ``${VAR:-default}`` in every string in the config.

    Only ``DCSVIZ_*`` variables are readable; other names raise unless a default
    is supplied (defaults do not touch the environment).
    """
    if isinstance(value, str):
        def _replace(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            if not name.startswith(ENV_ALLOWLIST_PREFIX):
                raise ValueError(
                    f"config may only reference DCSVIZ_* environment variables "
                    f"(got {name!r})")
            resolved = os.environ.get(name)
            if resolved is None:
                if default is not None:
                    return default
                raise ValueError(
                    f"environment variable {name!r} referenced by the "
                    f"configuration is not set")
            return resolved
        return _ENV_RE.sub(_replace, value)
    if isinstance(value, list):
        return [resolve_env(item) for item in value]
    if isinstance(value, dict):
        return {key: resolve_env(item) for key, item in value.items()}
    return value


def parse_config(payload: dict[str, Any]) -> DatasetConfig:
    """Resolve env references and validate a raw config.json payload."""
    return DatasetConfig(**resolve_env(payload))


def load_configs_from_dir(directory: str | Path) -> list[DatasetConfig]:
    """Load every ``*.json`` config file in a directory, skipping bad files."""
    path = Path(directory)
    configs: list[DatasetConfig] = []
    if not path.is_dir():
        return configs
    for candidate in sorted(path.glob("*.json")):
        try:
            payload = json.loads(candidate.read_text())
            configs.append(parse_config(payload))
        except Exception as exc:  # keep the app bootable on one bad file
            print(f"  config {candidate.name}: skipped ({exc})")
    return configs
