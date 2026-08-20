# Anaviz — Complete Project Explained

> **Dataset-Independent Time-Series Visualization & Evidence Layer**
> A 2-container architecture for exploring ANY HTTP time-series data source.

---

## Table of Contents

1. [What is Anaviz?](#1-what-is-anaviz)
2. [The Dataset — ATLAS HLT p-beat](#2-the-dataset--atlas-hlt-p-beat)
3. [Architecture Overview](#3-architecture-overview)
4. [Container 1: The Datasource](#4-container-1-the-datasource)
5. [Container 2: The Project (Visualization App)](#5-container-2-the-project-visualization-app)
6. [The Config System — How the Project Talks to ANY Data Source](#6-the-config-system--how-the-project-talks-to-any-data-source)
7. [The Query Pipeline — From Click to Chart](#7-the-query-pipeline--from-click-to-chart)
8. [Downsampling Algorithms](#8-downsampling-algorithms)
9. [Rollup Tiers — Opt-in Aggregation](#9-rollup-tiers--opt-in-aggregation)
10. [The Frontend — Chart, Heatmap, Histograms](#10-the-frontend--chart-heatmap-histograms)
11. [Data Fidelity & Gap Preservation](#11-data-fidelity--gap-preservation)
12. [Running the Project](#12-running-the-project)
13. [Test Suite](#13-test-suite)

---

## 1. What is Anaviz?

Anaviz is a **dataset-independent** time-series visualization tool. You bring your own HTTP data source + a `config.json` schema, and Anaviz adapts to it.

**Key Principle:** The project ships with ZERO preloaded datasets. Users register their own API endpoint + configuration through the UI or `POST /api/configs`.

```
┌─────────────────────────────────────────────────────┐
│                    YOUR DATA SOURCE                  │
│           (any HTTP time-series API)                 │
│                                                      │
│  Could be:                                           │
│  • CERN ATLAS HLT monitoring data                   │
│  • InfluxDB / Prometheus                             │
│  • Custom sensor API                                 │
│  • Any API returning timestamps + values             │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP
                       ▼
┌─────────────────────────────────────────────────────┐
│                    ANAVIZ                            │
│  config.json describes HOW to talk to your API      │
│  Generic cache hydrates on demand                   │
│  Visualization: chart + heatmap + histograms        │
└─────────────────────────────────────────────────────┘
```

---

## 2. The Dataset — ATLAS HLT p-beat

### What is it?

Real hardware monitoring data from the **ATLAS experiment** at CERN's Large Hadron Collider (LHC), available on Zenodo (record 7908064).

| Term | Full Name | What it is |
|------|-----------|------------|
| **HLT** | High Level Trigger | The real-time event selection system — decides which of ~40 million collisions/sec to keep |
| **p-beat** | Periodic heartbeat | Regular monitoring signals showing how many events each HLT node processes per second |
| **DCM** | Data Collection Module | Computing nodes within the HLT that collect monitoring signals |

**"HLT p-beat" = heartbeat/rate monitoring data from the HLT system's computing nodes.**

### CSV Structure

```python
# From datasource/anaviz_datasource/ingest_hlt.py:
# CSV format:
#     Row index  = ISO timestamp with tz  (2018-04-18 18:39:55.001542+02:00)
#     Columns    = full DCM node names    (DF_IS:HLT-24:tpu-rack-16...info)
#     Values     = float Hz rates, NaN = missing
```

| File | Size | Purpose |
|------|------|---------|
| `hlt_train_set.csv` | ~2.0 GB | Training data for ML models |
| `hlt_test_set.csv` | ~995 MB | Test/evaluation data |
| `hlt_val_set.csv` | ~632 MB | Validation data |

### Channel Naming

Each CSV column is a specific DCM node. Example:

```
DF_IS:HLT-24:tpu-rack-16.DCM-NoTS:HLT-24:tpu-rack-16:pc-tdq-tpu-16002.info
│     │       │                                     │
│     │       │                                     └─ node 16002
│     │       └─ physical rack location
│     └─ HLT subsystem ID #24
└─ data format / infrastructure
```

**Ingest code converts to readable names:**

```python
# From datasource/anaviz_datasource/ingest_hlt.py:
def short_name(full_col: str, idx: int) -> str:
    """Derive a short readable channel name from the full DCM column.
    E.g. DF_IS:HLT-24:tpu-rack-16.DCM-NoTS:HLT-24:tpu-rack-16:pc-tdq-tpu-16002.info
    → HLT_DCM_sub24_rack16_node16002
    The subsystem id (HLT-24 / HLT-36) is included because the same rack/node
    numbers recur across subsystems, and full_name is unique in the DB."""
    try:
        rack = "?"
        node = str(idx)
        sub  = "?"
        for part in full_col.split(":"):
            if part.startswith("HLT-"):
                sub = part.split("-")[-1]
            if "rack-" in part:
                rack = part.split("rack-")[-1].split(".")[0]
        if "tpu-" in full_col:
            node = full_col.split("tpu-")[-1].replace(".info", "")
        return f"HLT_DCM_sub{sub}_rack{rack}_node{node}"
    except Exception:
        return f"HLT_channel_{idx}"
```

### Element IDs

Channels are numbered starting at `900000`:

```python
# From ingest_hlt.py:
ELEMENT_ID_OFFSET = 900_000

# Column 0 → element_id 900000
# Column 1 → element_id 900001
# etc.
```

### Database Schema (Datasource)

```sql
-- From datasource/schema.sql:
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Core time-series store
CREATE TABLE eventhistory (
    element_id   INTEGER          NOT NULL,
    ts           TIMESTAMPTZ      NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    quality_flag SMALLINT         NOT NULL DEFAULT 0
);

SELECT create_hypertable('eventhistory', 'ts',
                         chunk_time_interval => INTERVAL '7 days');

-- Channel metadata
CREATE TABLE hardware_mapping (
    element_id INTEGER PRIMARY KEY,
    full_name  TEXT    NOT NULL UNIQUE
);

-- FSM state transitions (empty for HLT data — for future use)
CREATE TABLE transitions (
    transition_id  BIGINT      PRIMARY KEY,
    system_id      INTEGER     NOT NULL,
    old_state_int  SMALLINT    NOT NULL,
    new_state_int  SMALLINT    NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    operator_id    INTEGER
);

-- Subsystem registry (empty for HLT data — for future use)
CREATE TABLE subsystems (
    system_id INTEGER PRIMARY KEY,
    subsystem TEXT    NOT NULL
);
```

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        TWO CONTAINERS                                   │
│                                                                        │
│  ┌──────────────────────┐          ┌──────────────────────┐            │
│  │   DATASOURCE (:9000) │          │    PROJECT (:8000)   │            │
│  │   Real HLT data      │   HTTP   │    Generic viz app   │            │
│  │                      │◄────────►│                      │            │
│  │  • TimescaleDB       │          │  • FastAPI routes    │            │
│  │  • /archive/* API    │          │  • Generic cache DB  │            │
│  │  • HLT-specific code │          │  • No HLT knowledge  │            │
│  │  • DB port: 5434     │          │  • DB port: 5433     │            │
│  └──────────────────────┘          └──────────────────────┘            │
│                                                                        │
│  IMAGE: anaviz-datasource       IMAGE: anaviz-project                  │
│  (datasource/Dockerfile)        (docker/Dockerfile.project)            │
│                                                                        │
│  NEVER import from project      NEVER import from datasource           │
└─────────────────────────────────────────────────────────────────────────┘
```

**The two containers share NOTHING except a similar entrypoint.**

### docker-compose.yml

```yaml
# From docker-compose.yml:
services:
  datasource:
    build:
      context: ./datasource
      dockerfile: Dockerfile
    image: anaviz-datasource:latest
    command: ["uvicorn", "--app-dir", "/app",
              "anaviz_datasource.archive_server:app",
              "--host", "0.0.0.0", "--port", "9000"]
    ports:
      - "9000:9000"
      - "5434:5432"
    volumes:
      - anaviz_datasource_pgdata:/var/lib/postgresql/data
      - ./datasource/schema.sql:/docker-entrypoint-initdb.d/002_schema.sql:ro

  project:
    build:
      context: .
      dockerfile: docker/Dockerfile.project
    image: anaviz-project:latest
    command: ["uvicorn", "server.app:app",
              "--host", "0.0.0.0", "--port", "8000"]
    depends_on:
      datasource:
        condition: service_healthy
    ports:
      - "8000:8000"
      - "5433:5432"
    volumes:
      - anaviz_cache_pgdata:/var/lib/postgresql/data
      - ./db/cache_schema.sql:/docker-entrypoint-initdb.d/002_cache.sql:ro
      - ./configs:/app/configs:ro
```

### Entrypoint (both containers)

```bash
# From docker/entrypoint.sh:
# Shared entrypoint: start postgres in the background, wait until ready,
# then run the service command (uvicorn).
set -euo pipefail

docker-entrypoint.sh postgres -c shared_buffers=1GB -c work_mem=64MB &
PG_PID=$!

ready=0
for i in $(seq 1 120); do
  if pg_isready -U dcs -d dcs >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  echo "[anaviz] postgres did not become ready" >&2
  exit 1
fi
echo "[anaviz] postgres ready"

"$@" &    # ← This runs uvicorn for the respective service
APP_PID=$!
```

---

## 4. Container 1: The Datasource

### Package Structure

```
datasource/
├── Dockerfile
├── pyproject.toml
├── schema.sql                      ← TimescaleDB schema
└── anaviz_datasource/
    ├── __init__.py
    ├── __main__.py                 ← CLI: anaviz-download, anaviz-ingest
    ├── download_hlt.py             ← Parallel download from Zenodo
    ├── ingest_hlt.py               ← CSV → TimescaleDB
    └── archive_server.py           ← FastAPI /archive/* API
```

### Download Pipeline

```python
# From datasource/anaviz_datasource/download_hlt.py:
ZENODO_RECORD = "https://zenodo.org/records/7908064/files"

KNOWN_FILES: dict[str, tuple[int, str]] = {
    "hlt_test_set.csv":  (995_431_576,  "dab4db8c208ca8cb5f27f5285dc6ff3d"),
    "hlt_val_set.csv":   (631_923_178,  "be2d55a5eb4236ff88af96c3af309642"),
    "hlt_train_set.csv": (2_007_732_526,"9294a99cdd42bb0159678ede4d2c1f85"),
}

CHUNK = 1 << 20      # 1 MiB read buffer (md5)
SEGMENT = 64 << 20   # 64 MiB per parallel segment
```

**Key features:**
- Parallel segmented download (64 MiB chunks, 8 concurrent)
- Resumable: HTTP Range requests, stitches segments
- MD5 verification after each file
- Re-run safely — picks up where it left off

### Ingest Pipeline

```python
# From datasource/anaviz_datasource/ingest_hlt.py:
def ingest_file(path: Path, col_to_eid: dict[str, int],
                conn: psycopg.Connection) -> int:
    """Stream one CSV into eventhistory. Returns rows written."""
    # Uses temp staging table for idempotent COPY
    cur.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _hlt_ingest_stage ("
        "element_id INTEGER NOT NULL, ts TIMESTAMPTZ NOT NULL, "
        "value DOUBLE PRECISION NOT NULL, quality_flag SMALLINT NOT NULL) "
        "ON COMMIT DELETE ROWS")

    def flush_buffer() -> None:
        # COPY to staging → INSERT INTO eventhistory ON CONFLICT DO UPDATE
        cur.execute(
            "INSERT INTO eventhistory (element_id, ts, value, quality_flag) "
            "SELECT element_id, ts, value, quality_flag FROM _hlt_ingest_stage "
            "ON CONFLICT (element_id, ts) DO UPDATE SET "
            "value = EXCLUDED.value, quality_flag = EXCLUDED.quality_flag")
```

**Key features:**
- Reads CSV in 5000-row chunks (never loads 2 GB at once)
- Uses `ON CONFLICT DO UPDATE` for idempotent re-runs
- Temp staging table makes interrupted loads safe
- NaN values are skipped (not stored)

### Archive Server API

```python
# From datasource/anaviz_datasource/archive_server.py:

@app.get("/archive/channels")
async def channels():
    """List all DCM nodes."""
    # SELECT element_id, full_name FROM hardware_mapping ORDER BY element_id

@app.get("/archive/extent")
async def extent():
    """Time range of all data."""
    # SELECT extract(epoch FROM min(ts)), extract(epoch FROM max(ts))
    #        FROM eventhistory

@app.get("/archive/eventhistory")
async def eventhistory(element_id: int, t0: str, t1: str,
                       offset: int = 0, limit: int = 50_000):
    """Paginated raw rows for one channel."""
    # SELECT extract(epoch FROM ts), value, quality_flag
    # FROM eventhistory
    # WHERE element_id=%s AND ts>=to_timestamp(%s) AND ts<to_timestamp(%s)
    # ORDER BY ts OFFSET %s LIMIT %s

@app.get("/archive/eventhistory.csv")
async def eventhistory_csv(element_id: int, t0: str, t1: str):
    """Streamed CSV download."""
    # StreamingResponse with async generator
```

---

## 5. Container 2: The Project (Visualization App)

### Package Structure

```
server/
├── __main__.py
├── app.py                          ← FastAPI composition + routes
├── config.py                       ← Settings from env vars
├── api/
│   ├── contracts.py                ← Canonical Pydantic models
│   ├── config.py                   ← config.json validation (DatasetConfig)
│   ├── configurable.py             ← HTTP adapter + generic cache
│   └── common.py                   ← Coverage math, gap detection, z-score
├── downsample/
│   ├── __init__.py                 ← Pixel-budget downsampling dispatch
│   ├── lttb.py                     ← Largest-Triangle-Three-Buckets
│   ├── m4.py                       ← M4 algorithm
│   ├── minmax_lttb.py              ← MinMax-LTTB hybrid
│   └── rollup.py                   ← Opt-in bucket aggregation tiers
frontend/
├── index.html
├── app.js                          ← Dataset discovery, state, API calls
├── chart.js                        ← uPlot lifecycle, hover tooltip
├── heatmap.js                      ← Canvas heatmap overlay
├── histogram.js                    ← Per-entity histograms
├── utils.js                        ← Shared helpers, response adaptation
├── style.css
└── favicon.svg
db/
└── cache_schema.sql                ← Generic fixed-schema cache tables
```

### The Generic Cache Schema

```sql
-- From db/cache_schema.sql:

-- Raw time-series cache (shared across ALL datasets, isolated by dataset_key)
CREATE TABLE IF NOT EXISTS cache_series (
    dataset_key TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    measure_id  TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    quality     SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, ts)
);

-- Coverage tracking: which ranges have been fetched (avoids re-probing)
CREATE TABLE IF NOT EXISTS cache_coverage (
    dataset_key TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    measure_id  TEXT NOT NULL,
    t_min       TIMESTAMPTZ NOT NULL,
    t_max       TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queried', 'empty')),
    UNIQUE (dataset_key, entity_id, measure_id, t_min, t_max, status)
);

-- Opt-in rollup tiers: first/min/max/last/avg/count per time bucket
CREATE TABLE IF NOT EXISTS cache_rollup (
    dataset_key  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    measure_id   TEXT NOT NULL,
    bucket_s     DOUBLE PRECISION NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    n            INTEGER NOT NULL,
    v_first      DOUBLE PRECISION NOT NULL,
    first_ts     TIMESTAMPTZ NOT NULL,
    v_min        DOUBLE PRECISION NOT NULL,
    min_ts       TIMESTAMPTZ NOT NULL,
    v_max        DOUBLE PRECISION NOT NULL,
    max_ts       TIMESTAMPTZ NOT NULL,
    v_last       DOUBLE PRECISION NOT NULL,
    last_ts      TIMESTAMPTZ NOT NULL,
    v_avg        DOUBLE PRECISION NOT NULL,
    q_worst      SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, bucket_s, bucket_start)
);
```

**Key design:** Tables are shared across ALL datasets. Isolation is by `dataset_key = "<dataset_id>:<config_fingerprint>"`. A changed config creates a fresh namespace — never leaks stale rows.

### Canonical Contracts (Pydantic Models)

```python
# From server/api/contracts.py:

class DatasetAdapter(Protocol):
    """Source adapter consumed by generic API routes."""
    async def describe(self) -> DatasetSchema: ...
    async def entities(self, search, offset, limit) -> EntityPage: ...
    async def extent(self) -> TimeExtent: ...
    async def query(self, request: SeriesQuery) -> SeriesQueryResponse: ...
    async def matrix(self, request: MatrixQuery) -> MatrixResult: ...

class SeriesQuery(BaseModel):
    dataset_id: str = "default"
    entity_ids: list[str]                    # up to 64 entities
    measure_ids: list[str]
    range: TimeRange
    resolution: ResolutionRequest            # pixel_width × points_per_pixel
    downsampling: Literal["LTTB", "M4", "MINMAXLTTB", "RAW"]
    filters: dict[str, list[str]]

class SeriesPoints(BaseModel):
    t: list[float]
    value: list[float | None]
    min: list[float | None] | None          # band edge (lower)
    max: list[float | None] | None          # band edge (upper)
    first: list[float | None] | None
    last: list[float | None] | None
    min_t: list[float] | None               # timestamp of min
    max_t: list[float] | None               # timestamp of max
    first_t: list[float] | None
    last_t: list[float] | None
    quality: list[int | str | None]
    sample_count: list[int | None]

class CanonicalSeries(BaseModel):
    entity_id: str
    entity_label: str | None
    measure_id: str
    points: SeriesPoints
    resolution: str | None                  # "raw", "16s", "5m", etc.
    gaps: list[list[float]]                 # [[start, end], ...]
    quality_summary: dict[str, int]
    fidelity: dict[str, Any]                # provenance metadata
    metrics: QueryMetrics                   # rows_source, rows_scanned, etc.
```

### The ConfigurableHttpAdapter — The Heart of the System

```python
# From server/api/configurable.py:

class ConfigurableHttpAdapter(DatasetAdapter):
    """Implements the canonical adapter contract against an arbitrary HTTP API
    described by a validated DatasetConfig."""

    def __init__(self, config: DatasetConfig, cache: GenericDatasetCache, ...):
        self.config = config
        self.cache = cache
        self.dataset_key = cache.dataset_key

    async def query(self, request: SeriesQuery) -> SeriesQueryResponse:
        """Two-pass approach:
        Pass 1: Hydrate all uncovered ranges into the cache
        Pass 2: Serve each series from the appropriate tier (raw or rollup)
        """
        # Pass 1: hydrate
        for entity_id in request.entity_ids:
            for measure_id in request.measure_ids:
                await self._hydrate(entity_id, measure_id, start, end)

        # Pass 2: serve from chosen tier
        tier = select_tier(span_s, pixel_width, points_per_pixel, levels)
        for entity_id in request.entity_ids:
            for measure_id in request.measure_ids:
                if tier == "raw":
                    series.append(await self._raw_series(...))
                else:
                    series.append(await self._rollup_series(...))
```

---

## 6. The Config System — How the Project Talks to ANY Data Source

### config.json Structure

```python
# From server/api/config.py — the validated DatasetConfig model:

class DatasetConfig(BaseModel):
    version: int = 1
    dataset: DatasetInfo            # id, label, description
    source: HttpSource              # base_url, timeout, headers
    endpoints: Endpoints            # entities, extent, series paths
    mapping: Mapping                # field paths, timestamp format, measures
    pagination: Pagination          # offset-based, page size
    expected_step_seconds: float | None   # source sampling step (optional)
    rollup_enabled: bool = True          # master kill-switch
    rollup_levels: list[float] | None    # explicit bucket sizes in seconds
    row_cap: int = 200_000               # max rows per read
```

### Example Config for the HLT Datasource

```json
{
  "version": 1,
  "dataset": {
    "id": "default",
    "label": "ATLAS HLT p-beat",
    "description": "Heartbeat monitoring rates from HLT computing nodes"
  },
  "source": {
    "type": "http",
    "base_url": "http://datasource:9000",
    "timeout_seconds": 120
  },
  "endpoints": {
    "entities": {
      "path": "/archive/channels",
      "method": "GET"
    },
    "extent": {
      "path": "/archive/extent",
      "start_path": "t_min",
      "end_path": "t_max"
    },
    "series": {
      "path": "/archive/eventhistory",
      "shape": "columnar",
      "parameters": {
        "entity": "element_id",
        "start": "t0",
        "end": "t1"
      },
      "offset_param": "offset",
      "limit_param": "limit",
      "next_offset_path": "next_offset"
    }
  },
  "mapping": {
    "entity_id": "element_id",
    "entity_label": "name",
    "timestamp": "t",
    "timestamp_format": "unix",
    "measures": {
      "value": {
        "value_path": "value",
        "label": "Event Rate",
        "unit": "Hz"
      }
    },
    "quality": "quality_flag"
  },
  "pagination": {
    "type": "offset",
    "page_size": 50000,
    "max_pages": 20
  },
  "row_cap": 200000,
  "rollup_levels": [1, 4, 16, 64, 256]
}
```

### Environment Variable Substitution

Configs support `${DCSVIZ_VAR:-default}` syntax, but ONLY for `DCSVIZ_*` variables (security: prevents exfiltrating server secrets):

```python
# From server/api/config.py:
ENV_ALLOWLIST_PREFIX = "DCSVIZ_"

def resolve_env(value: Any) -> Any:
    """Expand ${VAR} / ${VAR:-default} in every string in the config.
    Only DCSVIZ_* variables are readable."""
    if isinstance(value, str):
        def _replace(match):
            name, default = match.group(1), match.group(2)
            if not name.startswith(ENV_ALLOWLIST_PREFIX):
                raise ValueError(
                    f"config may only reference DCSVIZ_* environment variables "
                    f"(got {name!r})")
            resolved = os.environ.get(name)
            if resolved is None:
                if default is not None:
                    return default
                raise ValueError(f"environment variable {name!r} not set")
            return resolved
        return _ENV_RE.sub(_replace, value)
```

### Fingerprinting — Cache Namespace Isolation

```python
# From server/api/config.py:
def fingerprint(self) -> str:
    """Stable hash of the resolved configuration (schema => namespace)."""
    canonical = json.dumps(
        json.loads(self.model_dump_json()), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

**A changed config = new fingerprint = fresh cache namespace. Never corrupts another dataset's data.**

---

## 7. The Query Pipeline — From Click to Chart

### Step 1: Frontend Sends Query

```javascript
// From frontend/app.js:
const seriesPromise = apiPost("/api/query", {
    dataset_id: datasetId,
    entity_ids: selected.map(entity => String(entity.entity_id)),
    measure_ids: [measureId],
    range: { start: view.t0, end: view.t1 },
    resolution: {
        strategy: "auto",
        pixel_width: params.px,    // e.g. 1000
        points_per_pixel: params.k, // e.g. 2
    },
    downsampling: params.algo,     // "M4", "LTTB", "MINMAXLTTB", or "RAW"
    filters: {},
}, signal);
```

### Step 2: Backend Routes to Adapter

```python
# From server/app.py:
@app.post("/api/query", response_model=SeriesQueryResponse)
async def canonical_query(request: SeriesQuery):
    return await _get_dataset_adapter(request.dataset_id).query(request)
```

### Step 3: Adapter Hydrates Cache (Two-Pass)

```python
# From server/api/configurable.py — _hydrate():
async def _hydrate(self, entity_id, measure_id, start, end):
    """Fetch uncovered ranges into the cache."""
    raw = self._denamespace(entity_id)  # strip "dataset_id:" prefix
    covered = await self.cache.coverage(entity_id, measure_id, start, end)
    gaps = coverage_gaps(start, end, covered)  # what the cache DOESN'T have
    for a, b in gaps:
        rows, truncated = await self._fetch_series(raw, measure_id, a, b)
        await self.cache.store(entity_id, measure_id, rows, intervals,
                               rollup_levels=levels)
```

### Step 4: No-Data-Loss SQL Bucket Aggregation

```python
# From server/api/configurable.py — read_bucketed():
async def read_bucketed(self, entity_id, measure_id, bucket_s,
                        start, end, cap):
    """Aggregate ALL cached rows into time buckets directly in Postgres.
    First/min/max/last/avg/count per bucket — every sample contributes."""
    cur = await conn.execute(
        "SELECT idx * %s AS bucket_start, "
        "(array_agg(v ORDER BY t))[1] AS v_first, "
        "(array_agg(t ORDER BY t))[1] AS first_ts, "
        "min(v) AS v_min, "
        "(array_agg(t ORDER BY v, t))[1] AS min_ts, "
        "max(v) AS v_max, "
        "(array_agg(t ORDER BY v DESC, t DESC))[1] AS max_ts, "
        "(array_agg(v ORDER BY t DESC))[1] AS v_last, "
        "(array_agg(t ORDER BY t DESC))[1] AS last_ts, "
        "avg(v) AS v_avg, count(*) AS n, max(q) AS q_worst "
        "FROM (SELECT floor(extract(epoch FROM ts) / %s)::bigint AS idx, "
        "extract(epoch FROM ts)::float8 AS t, value AS v, "
        "quality::smallint AS q FROM cache_series "
        "WHERE ... ) sub "
        "GROUP BY idx ORDER BY idx LIMIT %s",
        (bucket, bucket, self.dataset_key, entity_id, measure_id,
         start, end, cap))
```

### Step 5: Downsampling to Fit Pixel Budget

```python
# From server/downsample/__init__.py:
def downsample(payload: dict, algo: str, budget: int) -> tuple[dict, float]:
    """Apply downsampling to fit the pixel budget."""
    n = len(payload["t"])
    if n == 0 or algo == "RAW":
        return payload, 0.0

    if algo == "M4":
        if "min" in payload:
            idx, ts, vals = m4_series(
                payload["t"], payload["avg"], budget,
                payload["min"], payload["max"],
                payload["first"], payload["last"],
                payload["min_t"], payload["max_t"],
                payload["first_t"], payload["last_t"])
        else:
            idx, ts, vals = m4_series(payload["t"], payload["avg"], budget)
        result = {key: arr[idx] for key, arr in payload.items()}
        result["t"], result["avg"] = ts, vals

    elif algo == "MINMAXLTTB":
        idx, counts, starts = minmax_lttb_indices(payload["t"], payload["avg"], budget)
        # ... band assembly
    else:  # LTTB
        idx = lttb_indices(payload["t"], payload["avg"], budget)
        # ... band assembly
```

### Step 6: Response to Frontend

```python
# The response includes fidelity metadata:
CanonicalSeries(
    entity_id="default:900000",
    points=SeriesPoints(t=[...], value=[...], min=[...], max=[...], ...),
    resolution="16s",
    gaps=[[5000.0, 5100.0]],           # source gap preserved!
    quality_summary={"0": 35800, "2": 100},
    fidelity={
        "source": "config_http_cache",
        "tier": "16s",
        "aggregated": True,
        "interpolated": False,           # ← gaps are NEVER interpolated
        "missing_intervals_preserved": True,
        "truncated": False,
    },
    metrics=QueryMetrics(
        rows_source=35900,              # total rows in time range
        rows_scanned=2244,              # after bucket aggregation
        rows_returned=1000,             # after downsampling
        query_ms=45.2,
        source_ms=120.0,
        downsample_ms=2.3,
    )
)
```

---

## 8. Downsampling Algorithms

### LTTB — Largest-Triangle-Three-Buckets

Reduces N points to `budget` points while preserving visual shape by selecting the point that forms the largest triangle with the bucket average.

### M4 — Mean-Min-Max-Extrema

Selects up to 4 representative points per bucket: first, min, max, last. Preserves peaks and valleys that LTTB might miss.

### MINMAXLTTB — MinMax-LTTB Hybrid

Combines LTTB shape preservation with guaranteed min/max inclusion per segment.

### Band Assembly

When bucketed data includes min/max corners, downsamplers produce a "band" (confidence envelope):

```python
# From server/downsample/__init__.py:
def _apply_band(result, payload, starts, counts=None):
    """Recompute min/max envelope over the downsampled segments."""
    if counts is None:
        result["min"] = np.minimum.reduceat(payload["min"], starts)
        result["max"] = np.maximum.reduceat(payload["max"], starts)
    else:
        result["min"] = np.repeat(np.minimum.reduceat(payload["min"], starts), counts)
        result["max"] = np.repeat(np.maximum.reduceat(payload["max"], starts), counts)
    return result
```

---

## 9. Rollup Tiers — Opt-in Aggregation

### Tier Selection

```python
# From server/downsample/rollup.py:
def select_tier(span_s, pixel_width, points_per_pixel, levels=None):
    """Pick 'raw' or a bucket size (seconds) for a query.
    Coarsest tier with bucket <= span / (px * k).
    When no level can reduce the row count, 'raw' wins."""
    if span_s <= 0 or not levels:
        return "raw"
    levels = sorted(levels)
    target = span_s / max(pixel_width * points_per_pixel, 1)
    if target < levels[0] * TIER_MULTIPLIER:  # TIER_MULTIPLIER = 4
        return "raw"
    chosen = levels[0]
    for level in levels:
        if level <= target:
            chosen = level
    return chosen
```

### Bucket Aggregation

```python
# From server/downsample/rollup.py:
def aggregate_buckets(t, v, q, levels):
    """Aggregate sorted raw samples into per-level bucket rows.
    Returns {bucket_s: [bucket tuples]} ordered by bucket start.
    Min ties keep earliest timestamp, max ties the latest."""
    for bucket_s in levels:
        idx = (t // bucket_s).astype(np.int64)    # bucket assignment
        vmin = np.minimum.reduceat(ov, starts)    # per-bucket min
        vmax = np.maximum.reduceat(ov, starts)    # per-bucket max
        v_avg = np.add.reduceat(ov, starts) / counts  # per-bucket mean
        q_worst = np.maximum.reduceat(oq, starts)  # worst quality
```

### Human-Readable Tier Names

```python
def tier_name(bucket_s):
    """'16s', '5m', '1h', '1d', '1h8m16s', ..."""
```

---

## 10. The Frontend — Chart, Heatmap, Histograms

### Chart (uPlot)

```javascript
// From frontend/chart.js:
export function initPlot(data, selected, onNav, perEntityAxes, bandByEntity) {
    const wrap = document.getElementById('chart');
    const w = Math.max(400, document.getElementById('chartwrap').clientWidth);
    const h = Math.max(240, document.getElementById('chartwrap').clientHeight - 20);

    const series = [{}];
    const bands  = [];
    selected.slice(0, MAX_SERIES).forEach((s, i) => {
        const color = COLORS[i % COLORS.length];
        const base  = series.length;
        series.push({ label: s.label, stroke: color, width: 1.5 });
        if (bandByEntity?.[i]) {
            // Band (min/max envelope) — two invisible series + fill
            series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
            series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
            bands.push({ series: [base + 2, base + 1], fill: color + '26' });
        }
    });

    plot = new uPlot(opts, data, wrap);
}
```

**Key features:**
- Zoom with mouse wheel (factor-based)
- Pan with mouse drag
- Hover tooltip with timestamp + values + quality + evidence info
- Synced cursor with heatmap
- Auto-resize on panel toggle

### Heatmap (Canvas)

```javascript
// From frontend/heatmap.js:
// Renders a temporal_rolling_zscore matrix as a canvas heatmap.
// Color mapping: negative scores → blue, zero → white, positive → red.
// Missing cells (gaps) → transparent.
```

### Histograms

```javascript
// From frontend/histogram.js:
// Per-entity value distribution histograms.
// Rebuilt from last chart data — instant, no refetch.
// Hover synced with chart and heatmap.
```

### Response Adaptation

```javascript
// From frontend/utils.js:
export function seriesResponseToRenderer(response) {
    return (response?.series || []).map(item => {
        const points = item.points || {};
        return {
            entity_id: item.entity_id,
            resolution: item.resolution,
            algo: response?.provenance?.downsampling || 'M4',
            rows_raw: item.metrics?.rows_source || 0,
            rows_scanned: item.metrics?.rows_scanned || 0,
            rows_returned: item.metrics?.rows_returned || 0,
            query_ms: item.metrics?.query_ms || 0,
            archive_ms: item.metrics?.source_ms || 0,
            ds_ms: item.metrics?.downsample_ms || 0,
            truncated: Boolean(item.metrics?.truncated),
            expected_step_seconds: item.expected_step_seconds,
            gap_intervals: item.gaps || [],
            quality_summary: item.quality_summary || {},
            data_fidelity: item.fidelity || {},
            series: { t: points.t, avg: points.value, ... },
        };
    });
}
```

---

## 11. Data Fidelity & Gap Preservation

### Gap Detection

```python
# From server/api/common.py:
def find_gap_intervals(t, expected_step):
    """Find gaps in the time array where t[i+1] - t[i] > expected_step * 2.
    Returns [[gap_start, gap_end], ...] for the frontend to break lines."""
```

### Gap-Aware Interpolation

```javascript
// From frontend/utils.js:
export function interpTo(allT, tArr, vArr, rawMode, gaps = []) {
    // Linear interpolation between observed points,
    // but NEVER across declared source gaps.
    // Gaps produce null values → line breaks in the chart.
    const inGap = gapIdx < gaps.length &&
        x > gaps[gapIdx][0] && x < gaps[gapIdx][1];
    if (inGap) {
        vals[i] = null;  // ← gap preserved as null, never interpolated
    }
}
```

### Missing Values

```python
# From server/api/configurable.py — extract_rows():
# Upstream missing sentinels (e.g., -9999, NaN) become None:
if raw_v is None or (missing is not None and raw_v == missing):
    value = None   # absent, not zero
```

---

## 12. Running the Project

### Commands

```bash
# Start both containers
sudo docker compose up -d --build

# Or individually:
sudo docker compose up -d --build datasource   # :9000, :5434
sudo docker compose up -d --build project      # :8000, :5433

# Download HLT data (host-side)
pip install -e ./datasource
anaviz-download                                    # all three files
anaviz-download --only train                       # just train
anaviz-download --check                            # verify sizes + md5

# Ingest into datasource DB
anaviz-ingest --data-dir data/hlt --files all \
  --db-url postgresql://dcs:dcs@localhost:5434/dcs

# Verify with tests
pip install -e ".[test]"
pytest tests/ -q    # 72+ offline unit tests

# Code verification
python3 -m compileall server datasource/anaviz_datasource
for f in frontend/*.js; do node --check "$f"; done
```

### Database Access

```bash
# Project cache psql
sudo docker compose exec project psql -U dcs -d dcs

# Datasource psql
sudo docker compose exec datasource psql -U dcs -d dcs
```

---

## 13. Test Suite

**72+ offline unit tests** (no DB required):

| Test File | What it Tests |
|-----------|---------------|
| `test_config.py` | Config validation, env substitution, fingerprints, loader |
| `test_configurable.py` | HTTP extraction, pagination, query/matrix paths, gap preservation, opaque-ID translation |
| `test_rollup.py` | Tier selection math, bucket aggregation, adapter rollup query path |
| `test_canonical_contract.py` | Pydantic model validation, contract compliance |
| `test_downsample.py` | LTTB/M4/MINMAXLTTB correctness |
| `test_data_fidelity.py` | Data fidelity guarantees, no-data-loss |

### Running Tests

```bash
pytest tests/ -q
```

---

## Summary Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        ANAVIZ DATA FLOW                                  │
│                                                                          │
│  ┌─────────────┐    anaviz-download    ┌──────────────┐                  │
│  │   Zenodo    │ ──────────────────►   │  data/hlt/   │                  │
│  │   7908064   │                       │  3 CSV files  │                  │
│  └─────────────┘                       └──────┬───────┘                  │
│                                               │                          │
│                                    anaviz-ingest                        │
│                                               │                          │
│  ┌────────────────────────────────────────────▼──────────────────────┐  │
│  │  DATASOURCE DB (:5434)                                            │  │
│  │  eventhistory (TimescaleDB hypertable)                            │  │
│  │  hardware_mapping (element_id → readable name)                    │  │
│  └────────────────────────────────────────────┬──────────────────────┘  │
│                                               │                          │
│                                    /archive/* API (:9000)               │
│                                               │                          │
│  ┌────────────────────────────────────────────▼──────────────────────┐  │
│  │  ConfigurableHttpAdapter                                          │  │
│  │  1. Fetch from /archive/* based on config.json                    │  │
│  │  2. Cache into generic cache_series table                         │  │
│  │  3. Track coverage in cache_coverage                              │  │
│  │  4. Aggregate into cache_rollup if rollup_levels declared         │  │
│  └────────────────────────────────────────────┬──────────────────────┘  │
│                                               │                          │
│                                    POST /api/query                      │
│                                               │                          │
│  ┌────────────────────────────────────────────▼──────────────────────┐  │
│  │  Query Pipeline                                                   │  │
│  │  1. Hydrate uncovered ranges                                      │  │
│  │  2. Select tier (raw or rollup)                                   │  │
│  │  3. Read from cache (read_bucketed for dense ranges)              │  │
│  │  4. Downsample (LTTB/M4/MINMAXLTTB) to pixel budget              │  │
│  │  5. Return CanonicalSeries with fidelity metadata                 │  │
│  └────────────────────────────────────────────┬──────────────────────┘  │
│                                               │                          │
│  ┌────────────────────────────────────────────▼──────────────────────┐  │
│  │  Frontend                                                         │  │
│  │  • Chart (uPlot) — time series with band envelopes                │  │
│  │  • Heatmap (Canvas) — temporal rolling z-score matrix             │  │
│  │  • Histograms — per-entity value distributions                    │  │
│  │  • Evidence layer — quality flags, sample counts, gaps            │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```
