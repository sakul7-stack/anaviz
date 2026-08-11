# Anaviz Time-series Evidence Explorer

A schema-driven, dataset-independent visualization and evidence layer. Users
bring their own HTTP data source plus a `config.json` schema describing its
endpoints and fields; Anaviz validates the config, hydrates a generic cache on
demand, and serves canonical datasets, entities, measures, series, missingness,
quality, and provenance contracts to a dataset-agnostic frontend.

**Nothing is preloaded.** The project boots empty and you register your own
data source — an API URL + `config.json` — in the *Data sources* panel in the
UI (or via `POST /api/configs`). The datasource container serves the real ATLAS
HLT p-beat dataset (Zenodo record 7908064), but the project contains no
HLT-specific code path.

## Quick start

Run everything manually — one step at a time.

### 1. Install host tooling (once)

```bash
pip install -e ".[test]"
pip install -e ./datasource      # gives you anaviz-download and anaviz-ingest
```

### 2. Get the HLT CSVs into `data/hlt/`

Put these three files in `data/hlt/` (they are gitignored and already present
if you've downloaded them before):

- `hlt_train_set.csv` (~2 GB)
- `hlt_test_set.csv` (~995 MB)
- `hlt_val_set.csv` (~632 MB)

From the Drive mirror:
**https://drive.google.com/drive/folders/16ZZ-gTyvSyQi01OgaL0vRa0TDfQxlRNF?usp=sharing**

Or download from Zenodo (resumable): `anaviz-download --check` shows what's
missing, then re-run `anaviz-download` to resume.

Verify they are complete:

```bash
anaviz-download --check
```

### 3. Start the datasource (HLT database + /archive/* API)

```bash
sudo docker compose up -d --build datasource
# wait until healthy, then check:
curl http://localhost:9000/archive/extent    # → {"t_min":..., "t_max":...}
```

Non-null `t_min`/`t_max` means the DB is up (empty but running is fine at this
stage).

### 4. Ingest the CSVs into the datasource DB (one-time, slow)

Ingest only into the datasource DB on host port `:5434` — never into the
project cache (`:5433`):

```bash
anaviz-ingest --data-dir data/hlt --files all \
  --db-url postgresql://dcs:dcs@localhost:5434/dcs
```

- `--files train`      → train only (quickest way to see data)
- `--files train+test` → default
- `--files all`        → train + test + val

Ingest is resumable and conflict-safe: re-running it merges rows by
`(element_id, ts)` instead of duplicating. If the DB already has duplicate
rows from an interrupted older ingest, it refuses to guess — wipe the
datasource volume (`docker compose down -v && up`) and ingest again.

### 5. Start the project (viz app)

```bash
sudo docker compose up -d --build project
# → open http://localhost:8000
```

The project waits for the datasource to be healthy, then hydrates its cache
from `:9000` on demand. First query for a range is slower; later ones come from
the cache.

### 6. Done — everyday use

```bash
sudo docker compose up -d          # start both (data already ingested)
http://localhost:8000              # open the app
```

The real data lives in the datasource container's volume, so you never need to
download or ingest again unless you wipe that volume.

> **Why is the app empty when I open it?** Because nothing is preloaded — that's
> the point. Open the **Data sources** panel in the sidebar, paste the HLT
> `config.json` below, click **Register**, and the app comes alive. You can
> register any HTTP time-series API; the datasource on `:9000` is just one
> example. (If you want the datasource registered automatically instead, drop
> the same `*.json` config into `configs/` and restart the project.)
>
> Ready-to-paste config for the bundled HLT datasource:
>
> ```json
> {
>   "version": 1,
>   "dataset": { "id": "hlt", "label": "ATLAS HLT Archive" },
>   "source": { "type": "http", "base_url": "http://datasource:9000", "timeout_seconds": 300 },
>   "endpoints": {
>     "entities": { "path": "/archive/channels" },
>     "extent":   { "path": "/archive/extent", "start_path": "t_min", "end_path": "t_max" },
>     "series":   {
>       "path": "/archive/eventhistory",
>       "shape": "columnar",
>       "parameters": { "entity": "element_id", "start": "t0", "end": "t1" },
>       "offset_param": "offset", "limit_param": "limit", "next_offset_path": "next_offset"
>     }
>   },
>   "mapping": {
>     "entity_id": "element_id", "entity_label": "name",
>     "timestamp": "t", "timestamp_format": "unix",
>     "measures": { "value": { "value_path": "value", "type": "number" } },
>     "quality": "quality_flag"
>   },
>   "pagination": { "type": "offset", "page_size": 50000, "max_pages": 100 },
>   "row_cap": 200000
> }
> ```
>
> Use `http://datasource:9000`, **not** `localhost:9000` or `127.0.0.1:9000` —
> the project container reaches the datasource over the compose network, where
> `datasource` is the hostname. `base_url` may also use the environment form
> `${DCSVIZ_ARCHIVE:-http://datasource:9000}`.

### Start them separately (optional)

```bash
sudo docker compose up -d --build datasource   # HLT DB + API only (:9000, :5434)
sudo docker compose up -d --build project      # viz app only (:8000, :5433)
```

`compose up project` starts the datasource first automatically (dependency
tracking). To run the project truly alone, add `--no-deps`.

Makefile shortcuts do the same thing: `make db-up`, `make app-up`, `make up`,
`make down`, `make ps`, `make logs`, `make db-shell`, `make app-shell`,
`make rebuild`.

## Architecture

Two containers, **two fully independent images**:

- **datasource** (`anaviz-datasource`, built from `datasource/Dockerfile` —
  a standalone mini-project with its own `pyproject.toml`, entrypoint, and
  CLI) — owns the real HLT data and serves it through an `/archive/*` API on
  `:9000` (postgres on host `:5434`). This image contains **only** the
  `anaviz_datasource` package — no project code at all. Source of truth;
  never truncated.
- **project** (`anaviz-project`, built from `docker/Dockerfile.project`) —
  the dataset-independent viz app on `:8000` with a generic cache DB
  (`:5433`). This image contains **only** `server/`, `frontend/`, and
  `configs/` — no HLT code at all. It reads dataset configs from `configs/`
  and hydrates `cache_series` over HTTP from the configured sources, so
  `/api/clear-cache` can never destroy real data.

The two images share nothing except the generic `docker/entrypoint.sh`
(which just starts postgres and runs the service command).

```
┌───────────────────────────┐         ┌───────────────────────────┐
│  datasource container     │  :9000  │  project container        │
│  real HLT data (Timescale)│◄────────│  viz app + generic cache  │
│  /archive/* API           │  HTTP   │  /api/* (uPlot SPA)       │
│  :5434 (host, ingest)     │         │  :5433 (host, cache)      │
└───────────────────────────┘         └───────────────────────────┘
```

| Service    | Image                | HTTP        | Postgres (host) | Volume                    |
|------------|----------------------|-------------|-----------------|---------------------------|
| datasource | `anaviz-datasource`  | `:9000`     | `:5434`         | `anaviz_datasource_pgdata`|
| project    | `anaviz-project`     | `:8000`     | `:5433`         | `anaviz_cache_pgdata`     |

The project does **not** precompute rollups; downsampling (M4 / LTTB /
MinMaxLTTB) runs in memory over cached rows. Nothing is precomputed.

### Dataset-independent contract

The project service exposes a canonical visualization boundary:

1. `DatasetAdapter` describes datasets, entities, measures, dimensions, and
   capabilities.
2. `SeriesQuery` requests opaque entity IDs, measure IDs, a time range, pixel
   budget, and downsampling strategy.
3. `SeriesQueryResponse` returns values, envelopes, quality, sample counts,
   source gaps, fidelity metadata, timings, and provenance without exposing a
   database schema.
4. Typed evidence models keep detector scores, flags, thresholds, intervals,
   annotations, and ground truth semantically distinct.

`server/api/contracts.py` contains the canonical models and adapter/cache
protocols. `server/api/config.py` defines the validated `config.json` schema.
`server/api/configurable.py` implements `ConfigurableHttpAdapter`, the only
canonical layer that translates source-specific identifiers — it is driven
entirely by the config, never by hardcoded source knowledge.

### The config.json schema

A config describes an arbitrary HTTP time-series API declaratively. Example
(describing the datasource `/archive/*` API — a good starting point for your
own config):

```json
{
  "version": 1,
  "dataset": { "id": "default", "label": "ATLAS HLT Archive" },
  "source": { "type": "http", "base_url": "${DCSVIZ_ARCHIVE:-http://datasource:9000}", "timeout_seconds": 300 },
  "endpoints": {
    "entities": { "path": "/archive/channels" },
    "extent":   { "path": "/archive/extent", "start_path": "t_min", "end_path": "t_max" },
    "series":   {
      "path": "/archive/eventhistory",
      "shape": "columnar",
      "parameters": { "entity": "element_id", "start": "t0", "end": "t1" },
      "offset_param": "offset", "limit_param": "limit", "next_offset_path": "next_offset"
    }
  },
  "mapping": {
    "entity_id": "element_id", "entity_label": "name",
    "timestamp": "t", "timestamp_format": "unix",
    "measures": { "value": { "value_path": "value", "type": "number" } },
    "quality": "quality_flag"
  },
  "pagination": { "type": "offset", "page_size": 50000, "max_pages": 100 },
  "row_cap": 200000
}
```

Supported configuration:

- **endpoints.entities** — `path`, optional `items_path` (default: response body
  is the list).
- **endpoints.extent** — `path`, `start_path`, `end_path`.
- **endpoints.series** — `path`, `shape` (`row` | `columnar`), the canonical
  `parameters` map (`entity`/`start`/`end` → source query-param names),
  `items_path` for row shape, `next_offset_path` for pagination.
- **mapping** — `entity_id`, optional `entity_label`, `timestamp`, timestamp
  format (`unix` | `epoch_ms` | `iso`), `measures` (id → label/unit/type /
  `value_path`), optional `quality`, optional `quality_map` (upstream value →
  canonical int), optional `missing_value` sentinel.
- **pagination** — `type` (`offset` | `none`), `page_size`, `max_pages`.
- **expected_step_seconds** — expected sampling interval for gap detection.
- **row_cap** — per-entity per-range row cap (truncation is declared in
  fidelity metadata, never silent).
- **capabilities** — what the adapter advertises.
- `$ {VAR}` / `$ {VAR:-default}` environment references are resolved in any
  string value; secrets belong in environment variables, not config files.

Configs are declarative data only — no code, SQL, or templates are executed.
`POST /api/configs` validates a payload; a changed config gets a new fingerprint
and therefore a fresh cache namespace, so re-registering never corrupts old
cached rows.

### Request lifecycle

1. The browser discovers datasets, schema, entities, and extent through
   `/api/datasets/*`.
2. The browser sends opaque entity IDs and a pixel budget to `POST /api/query`.
3. `ConfigurableHttpAdapter` validates the canonical request and translates its
   own `<dataset_id>:<id>` identifiers.
4. The adapter calculates uncovered cache intervals (coverage + probed-empty)
   and fetches only those intervals from the source, following offset
   pagination.
5. Rows are merged conflict-safely into the fixed-schema cache
   (`cache_series`); coverage intervals (`cache_coverage`) record queried and
   known-empty ranges.
6. Gap, quality, sample-count, timing, fidelity, and provenance metadata are
   calculated before the display downsampler runs.
7. The frontend inserts null-only gap markers, renders the main chart, and then
   resolves optional matrix and histogram views independently.

Nulls and gaps are evidence: upstream missing sentinels become absent rows, and
missing time buckets are rendered as null — never zero or forward-filled.

### Module boundaries

| Module | Responsibility |
|--------|----------------|
| `server/app.py` | FastAPI lifecycle, dataset/config registry, canonical routes, legacy facades, static files |
| `server/api/contracts.py` | Canonical Pydantic models and adapter/cache protocols |
| `server/api/config.py` | Validated `config.json` model, fingerprinting, env resolution, configs-dir loader |
| `server/api/configurable.py` | `ConfigurableHttpAdapter`, generic cache, pagination, extraction, matrix builder |
| `server/api/common.py` | Pure helpers: timestamps, gaps, coverage arithmetic, heatmap math |
| `server/downsample/` | M4, LTTB, MinMaxLTTB downsampling |
| `frontend/app.js` | Dataset discovery, Data Sources panel, canonical requests, state, panel coordination |
| `frontend/chart.js` | uPlot lifecycle, line/envelope rendering, provenance tooltips |
| `frontend/heatmap.js` | Canvas time-by-entity matrix rendering |
| `frontend/histogram.js` | Exact-node distribution views |
| `frontend/utils.js` | API transport and canonical-response renderer mappers |

## Features

- Bring-your-own data source: register a URL + `config.json` in the UI or via
  `POST /api/configs` — no code changes, no per-source database schema
- Three downsampling algorithms: M4, LTTB, MinMaxLTTB (in-memory, no rollups)
- Per-entity temporal z-score heatmap with explicit missing cells
- Scroll-wheel zoom, drag pan, hover tooltip
- Cache-aside hydration: coverage intervals + known-empty ranges, conflict-safe
  `(dataset_key, entity_id, measure_id, ts)` inserts
- Interval-based cache coverage that preserves internal source gaps
- Series metadata for expected step, missing intervals, sample counts, quality,
  and source/cache provenance
- Fingerprinted cache namespaces: config changes never corrupt old rows

## Prerequisites

- Docker Engine with `docker compose` (v2). May need `sudo`.
- Python 3.11+ on the host (for download + ingest tooling).
- ~6 GB free disk for the CSVs, plus room for the DB volumes.

## First-run details

### Verifying the datasource

```bash
curl -s http://localhost:9000/archive/extent       # → {"t_min":..., "t_max":...}
curl -s http://localhost:9000/archive/channels | head
```

Non-null `t_min`/`t_max` means the data is served correctly.

### Adding a data source

Two ways:

1. **UI**: open the *Data sources* panel in the sidebar, paste a `config.json`,
   click *Register*, then pick it from the dataset dropdown.
2. **API**: `POST /api/configs` with `{"config": {...}}`; list with
   `GET /api/configs`; remove with `DELETE /api/configs/{id}`.

Configs are also loaded from `configs/*.json` at startup (`DCSVIZ_CONFIG_DIR`,
mounted read-only into the project container) — but the repo ships **no**
config there; the directory is intentionally empty. Re-registering a dataset
with a changed config starts a fresh cache namespace automatically.

## API endpoints

Project (`:8000`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/datasets` | Discover configured datasets and capabilities |
| GET | `/api/configs` | List registered configs (metadata only) |
| POST | `/api/configs` | Validate and register a dataset from a config.json |
| DELETE | `/api/configs/{id}` | Remove a dataset and drop its cache namespace |
| GET | `/api/datasets/{id}/schema` | Canonical time/entity/measure/dimension schema |
| GET | `/api/datasets/{id}/entities` | Searchable, paginated canonical entities |
| GET | `/api/datasets/{id}/extent` | Canonical dataset time extent |
| POST | `/api/query` | Canonical multi-entity, multi-measure series query |
| POST | `/api/matrix` | Canonical time-by-entity matrix/transform query |
| GET | `/api/extent` | Legacy global time-range facade (default dataset) |
| GET | `/api/channels` | Legacy channel-list facade (default dataset) |
| GET | `/api/series` | Legacy series facade with quality, sample-count, and gap metadata |
| GET | `/api/heatmap` | Temporal z-score entity heatmap grid (default dataset) |
| GET | `/api/status` | Background-work status |
| GET | `/api/clear-cache` | Flush project cache (safe — datasource untouched) |

Datasource (`:9000`, the `/archive/*` contract):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/archive/channels` | Channel list (element_id → name) |
| GET | `/archive/subsystems` | Subsystem list |
| GET | `/archive/extent` | Global time range |
| GET | `/archive/eventhistory` | Paginated raw rows (`offset`/`limit`) |
| GET | `/archive/eventhistory/count` | Row count for a channel/range |
| GET | `/archive/eventhistory.csv` | Streamed CSV for a channel/range |
| GET | `/archive/transitions` | FSM state transitions |

### Canonical API examples

Discover the configured dataset and its schema:

```bash
curl -s http://localhost:8000/api/datasets | python -m json.tool
curl -s http://localhost:8000/api/datasets/default/schema | python -m json.tool
curl -s 'http://localhost:8000/api/datasets/default/entities?limit=5' | python -m json.tool
```

Query one or more entities. Entity IDs must come from the entities endpoint;
they are opaque and must not be parsed by clients:

```bash
curl -s http://localhost:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_id": "default",
    "entity_ids": ["default:900000"],
    "measure_ids": ["value"],
    "range": {"start": 1524069595, "end": 1524070195},
    "resolution": {
      "strategy": "auto",
      "pixel_width": 1200,
      "points_per_pixel": 2
    },
    "downsampling": "M4",
    "filters": {}
  }' | python -m json.tool
```

A canonical series contains `points.t`, `points.value`, optional min/max
bands, exact quality/sample-count arrays, source `gaps`, fidelity declarations,
query metrics, and adapter provenance. Nulls and gaps are evidence; clients
must not replace them with zero or forward-filled values.

Build a time-by-entity matrix:

```bash
curl -s http://localhost:8000/api/matrix \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_id": "default",
    "entity_ids": ["default:900000"],
    "measure_id": "value",
    "range": {"start": 1524069595, "end": 1524073195},
    "transform": "temporal_rolling_zscore",
    "pixel_width": 800
  }' | python -m json.tool
```

The example timestamps and entity ID are placeholders; use values returned by
the running datasource.

## Environment variables

```
DCSVIZ_DB           postgresql://dcs:dcs@localhost:5433/dcs  (project cache)
DCSVIZ_ARCHIVE      http://datasource:9000                   (default config base_url)
DCSVIZ_CONFIG_DIR   configs                                  (dataset config dir)
DCSVIZ_SRC_DB       postgresql://dcs:dcs@localhost:5434/dcs  (datasource, ingest)
```

Compose sets `DCSVIZ_DB`, `DCSVIZ_ARCHIVE`, and `DCSVIZ_CONFIG_DIR` for the
containers. `DCSVIZ_SRC_DB` is a host-side convenience for ingestion tooling;
the documented ingest command passes its datasource URL explicitly. Dataset
configs may reference these variables with `${VAR}` / `${VAR:-default}`.

## Adding a dataset

No code changes are needed. Write a `config.json` describing the source (see
*The config.json schema*), then register it at runtime via the **Data sources**
panel in the UI or `POST /api/configs` (or drop it into `configs/` and restart
the project).

When writing a config, keep source-specific IDs opaque and let the adapter
namespace them (`<dataset_id>:<id>`). Preserve nulls, quality, source gaps,
sample counts, and provenance in the source responses. Test your config against
a small synthetic source first, covering irregular sampling, missing intervals,
quality flags, unsupported measures/filters, and opaque-ID rejection.

Do not add dataset-specific branches to `frontend/app.js`, chart renderers, or
canonical models. New behavior should be capability- or schema-driven.

## Project layout

```
server/                 the project: FastAPI composition, dataset/config registry,
                        canonical routes, facades
  api/                   canonical contracts, config model, configurable adapter,
                         pure helpers
  downsample/            M4, LTTB, MinMaxLTTB downsampling
datasource/              STANDALONE HLT datasource mini-project — own pyproject.toml,
                         own Dockerfile + entrypoint, own CLI. Independent of the
                         project in every direction; only talks to it over HTTP.
  anaviz_datasource/     the python package (archive server, ingest, download)
  schema.sql             HLT schema (eventhistory, hardware_mapping, ...)
  Dockerfile             its own image (build context = this directory)
frontend/                dataset-agnostic UI (no HLT knowledge)
  app.js                 discovery, Data Sources panel, state, requests
  chart.js               uPlot main chart
  heatmap.js             Canvas matrix renderer
  histogram.js           exact-node distributions
  utils.js               transport, response mapping, gap-safe alignment
configs/                 dataset configs (empty by default — nothing preloaded; the UI
                         registers configs at runtime, or drop a *.json here)
db/                      generic cache schema for the project
tests/                   offline contracts, config, adapter, fidelity, downsampling
data/hlt/                downloaded CSVs (gitignored)
docker/                   image and entrypoint
docs/                     reference papers (read-only)
```

## Development and verification

Install editable development dependencies (use the existing venv if you have
one — the `.venv` directory already exists in this repo):

```bash
source .venv/bin/activate
pip install -e ".[test]"
```

Run all checks that do not require Docker or a database:

```bash
pytest tests/ -q                     # 48 offline unit tests
python3 -m compileall -q server datasource/anaviz_datasource
for file in frontend/*.js; do node --check "$file"; done
git diff --check
```

The tests cover config validation and fingerprinting, the configurable adapter
(endpoint extraction, pagination, opaque-ID translation, gap preservation,
matrix missingness), canonical contracts, legacy route coexistence,
cache-ownership safety, coverage interval arithmetic, quality/sample metadata,
and all downsampling algorithms.

After backend or frontend changes, rebuild the project service. This does not
re-ingest or replace the datasource volume:

```bash
sudo docker compose up -d --build project
sudo docker compose ps
curl -fsS http://localhost:9000/archive/extent | python -m json.tool
curl -fsS http://localhost:8000/api/datasets | python -m json.tool
curl -fsS http://localhost:8000/api/status | python -m json.tool
```

For a browser deployment, hard-refresh after rebuilding (`Ctrl+Shift+R`) so
cached JavaScript does not mask the new image.

### Data-safety rules

| Action | Datasource effect | Project-cache effect |
|--------|-------------------|----------------------|
| Rebuild `project` | none | cache volume retained |
| Restart Compose | none | both volumes retained |
| `GET /api/clear-cache` | none | cache truncated and rehydrated on demand |
| `DELETE /api/configs/{id}` | none | only that dataset's namespace dropped |
| `docker compose down` | none | volumes retained |
| `docker compose down -v` | **deletes the datasource volume** | deletes cache volume |

Do not use `docker compose down -v` unless destroying and re-ingesting the raw
dataset is explicitly intended.

## Troubleshooting

- **Port already in use**: `sudo docker ps -a`, stop/kill the stale container,
  then `sudo docker compose up -d`.
- **`/archive/extent` returns nulls**: data not ingested — run the ingest
  command from Quick start step 4.
- **App shows no data but datasource is fine**: cache is empty for that range —
  query the range in the app (hydration is on demand). If stuck:
  `curl -s "http://localhost:8000/api/clear-cache"` and retry.
- **Registering a config fails**: check the error message — endpoints must
  expose the mapped fields, `series.parameters` must map `entity`/`start`/`end`,
  and `base_url` must be an `http(s)` URL without embedded credentials.
- **Reset only visualization cache**: use
  `curl -fsS http://localhost:8000/api/clear-cache`; do not delete volumes.
- **Destroy everything intentionally**: `docker compose down -v` removes both
  datasource and cache volumes and requires a complete datasource re-ingest.
