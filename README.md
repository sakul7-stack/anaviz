# Anaviz

Dataset-independent time-series visualization. Bring your own HTTP data
source plus a `config.json` that describes its endpoints and fields; Anaviz
validates the config, caches data on demand, and serves a canonical API to a
generic frontend (line chart, heatmap, histogram).

**Nothing is preloaded.** The app boots empty. You register your own data
source — an API URL + `config.json` — in the *Data sources* panel (or via
`POST /api/configs`). The bundled datasource container serves the ATLAS HLT
p-beat dataset, but Anaviz itself contains zero HLT-specific code.

## Repository layout

```
anaviz/        the visualization PROJECT — its own docker-compose.yml, Dockerfile,
               Makefile, .dockerignore; server/, frontend/, configs/, db/, tests/
               (compose project: anaviz-project, app :8000, cache DB :5433)
datasource/    the example HLT data API — its own docker-compose.yml, Dockerfile,
               Makefile, .dockerignore; anaviz_datasource/, schema.sql, data/
               (compose project: anaviz-datasource, API :9000, DB :5434)
docs/          reference PDFs and notes; docs/demo/ holds presentation material
Makefile       root convenience: starts both projects on a shared network
```

The two projects are fully independent: each has its own compose file,
Dockerfile, Makefile, and build context, and neither imports the other.
`anaviz/` is a complete project on its own — it talks to a source only over
HTTP via a `config.json`, and the bundled `datasource/` is just one example
source. They discover each other over an external Docker network
(`anaviz_shared`) so the project can reach the datasource at
`http://datasource:9000`.

**No shared/root compose:** run each project from its own folder (or use the
root `Makefile` to start both). Each compose binds its postgres volume as
**external** to the existing physical volume, so bringing the stack up **never
re-ingests** your data.

## Quick start

### 1. Install host tooling (once)

```bash
pip install -e "./anaviz[test]"      # project host tooling (server package + tests)
pip install -e ./datasource          # provides anaviz-download and anaviz-ingest
```

### 2. Get the HLT CSVs into `datasource/data/hlt/`

Three files (gitignored): `hlt_train_set.csv` (~2 GB), `hlt_test_set.csv`
(~995 MB), `hlt_val_set.csv` (~632 MB). Download them (resumable) from inside
the datasource project:

```bash
cd datasource
python -m anaviz_datasource download --out data/hlt   # or: make download
python -m anaviz_datasource download --check          # verify completeness
```

### 3. Start the datasource (HLT DB + `/archive/*` API)

```bash
make -C datasource up          # docker network + datasource (:9000, DB :5434)
curl http://localhost:9000/archive/extent    # → {"t_min":..., "t_max":...}
```

Non-null `t_min`/`t_max` means the DB is up. The data volume is external, so
this reuses your already-ingested data.

### 4. Ingest the CSVs into the datasource DB (one-time, slow)

Ingest into the datasource DB (`:5434`) — never into the project cache
(`:5433`). Run from the datasource folder:

```bash
cd datasource
make ingest        # = python -m anaviz_datasource ingest --data-dir data/hlt \
                   #     --files all --db-url postgresql://dcs:dcs@localhost:5434/dcs
```

`--files train` is the quickest way to see data; `train+test` is the default;
`all` is train + test + val. Ingest is resumable and conflict-safe — re-running
merges rows instead of duplicating.

### 5. Start the project (viz app)

```bash
make -C anaviz up              # docker network + project (:8000, cache :5433)
# → open http://localhost:8000
```

First query for a range is slower (hydration); later ones come from the cache.

### 6. Everyday use

```bash
make up                        # start BOTH projects (datasource then anaviz)
http://localhost:8000          # open the app
# make down                    # stop both (volumes retained)
```

> **Why is the app empty?** Nothing is preloaded — that's the point. The repo
> ships a ready-to-use **`example.json`** at the root that targets the bundled
> `:9000` datasource. Fastest ways to register it:
>
> ```bash
> # A) via the API (project must be up)
> curl -X POST http://localhost:8000/api/configs \
>   -H 'Content-Type: application/json' \
>   -d "{\"config\": $(cat example.json)}"
>
> # B) auto-load on boot — drop it into the project's config dir and restart
> cp example.json anaviz/configs/ && make -C anaviz up
> ```
>
> Or open the **Data sources** panel in the UI, paste the contents of
> `example.json`, and click **Register**. The equivalent config is also shown
> below for reference:
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
>   "row_cap": 200000,
>   "rollup_levels": [60, 300, 3600]
> }
> ```
>
> Use `http://datasource:9000`, not `localhost:9000` — the project reaches the
> datasource over the compose network. `rollup_levels` is optional (see
> *Rollups* below). `base_url` may use env form
> `${DCSVIZ_ARCHIVE:-http://datasource:9000}`.

## Architecture

Two containers, two fully independent images:

- **datasource** (`anaviz-datasource`) — owns the real HLT data and serves it
  via `/archive/*` on `:9000` (postgres on `:5434`). Contains only the
  `anaviz_datasource` package. Source of truth.
- **project** (`anaviz-project`) — the dataset-independent viz app on `:8000`
  with a generic cache DB (`:5433`). Contains only `server/`, `frontend/`, and
  `configs/`. Hydrates its cache over HTTP from configured sources, so
  `/api/clear-cache` can never destroy real data.

```
┌───────────────────────────┐         ┌───────────────────────────┐
│  datasource container     │  :9000  │  project container        │
│  real HLT data (Timescale)│◄────────│  viz app + generic cache  │
│  /archive/* API           │  HTTP   │  /api/* (uPlot SPA)       │
│  :5434 (host, ingest)     │         │  :5433 (host, cache)      │
└───────────────────────────┘         └───────────────────────────┘
```

The project exposes a canonical contract (`server/api/contracts.py`):
`DatasetAdapter` describes datasets/entities/measures; `SeriesQuery` /
`SeriesQueryResponse` move opaque IDs, time ranges, pixel budgets, values,
envelopes, quality, gaps, fidelity, and provenance — never a database schema.
The only source-specific knowledge lives in a `config.json` +
`ConfigurableAdapter` (`server/adapter/adapter.py`).

## Server structure

```
server/
├── api/                    Routes and models
│   ├── app.py                FastAPI routes + startup
│   ├── config.py             config.json validation (Pydantic)
│   └── contracts.py          Canonical Pydantic models
├── adapter/                Data logic (HTTP + cache)
│   ├── adapter.py            HTTP client, describe, entities, extent
│   ├── cache.py              Postgres cache (read/write/delete)
│   ├── query.py              Series + matrix query execution
│   ├── common.py             Pure helpers (drain, spawn, parse_ts)
│   ├── sql/
│   │   ├── schema.sql        DDL for cache tables
│   │   └── queries.py        Named SQL constants
│   └── downsample/           M4, LTTB, MinMaxLTTB, rollup
├── config.py               Settings (env vars)
├── app.py                  Entry point (uvicorn)
└── __main__.py             CLI (python -m server serve)
```

## Config

A config describes an arbitrary HTTP time-series API declaratively — data
only, never code. Example (the same HLT datasource):

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
  "row_cap": 200000,
  "rollup_levels": [60, 300, 3600]
}
```

Supported keys:

- **endpoints.entities** — `path`, optional `items_path` (default: the
  response body is the list).
- **endpoints.extent** — `path`, `start_path`, `end_path`.
- **endpoints.series** — `path`, `shape` (`row` | `columnar`), `parameters`
  (maps `entity`/`start`/`end` to source query-param names), `items_path` for
  row shape, `next_offset_path` for pagination.
- **mapping** — `entity_id`, optional `entity_label`, `timestamp`,
  `timestamp_format` (`unix` | `epoch_ms` | `iso`), `measures`
  (id → label/unit/type/`value_path`), optional `quality`, `quality_map`,
  `missing_value` sentinel.
- **pagination** — `type` (`offset` | `none`), `page_size`, `max_pages`.
- **expected_step_seconds** — expected sampling interval for gap detection.
- **row_cap** — density threshold. Ranges with more cached rows than this are
  aggregated in Postgres over **all** rows into one time bucket per pixel
  (exact min/max envelope) — visualization is never truncated.
- **rollup_levels** — *opt-in* on-demand rollup tiers: explicit bucket sizes
  in seconds (e.g. `[60, 300, 3600]`). Declaring it opts the dataset in;
  tiers are never derived automatically. Omit it to serve every range from
  raw rows. `rollup_enabled: false` force-disables rollups even when levels
  are declared.
- **capabilities** — what the adapter advertises.
- **Env references** — `${VAR}` / `${VAR:-default}` are resolved in any
  string value; put secrets in environment variables, not configs.

A changed config gets a new fingerprint and therefore a fresh cache
namespace, so re-registering never corrupts old cached rows.

## Rollups

When a dataset declares `rollup_levels`, every hydrated range is also
aggregated into `cache_rollup` buckets (first/min/max/last/avg/count per
bucket). Big ranges are then served from the coarsest tier that keeps ≥1
bucket per pixel, then display-downsampled in memory (M4 / LTTB / MinMaxLTTB).
Nothing is auto-derived: no `rollup_levels`, no tiers.

Dense *raw* ranges (no rollups opted in) are never truncated either — see
`row_cap` above.

## API

Project (`:8000`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/datasets` | Discover configured datasets |
| GET | `/api/configs` | List registered configs (metadata only) |
| POST | `/api/configs` | Validate and register a dataset from a config.json |
| DELETE | `/api/configs/{id}` | Remove a dataset and drop its cache namespace |
| GET | `/api/datasets/{id}/schema` | Canonical schema |
| GET | `/api/datasets/{id}/entities` | Searchable, paginated entities |
| GET | `/api/datasets/{id}/extent` | Dataset time extent |
| POST | `/api/query` | Canonical multi-entity series query |
| POST | `/api/matrix` | Time-by-entity matrix/transform query |
| GET | `/api/extent` | Legacy global extent (default dataset) |
| GET | `/api/channels` | Legacy channel list (default dataset) |
| GET | `/api/series` | Legacy series facade |
| GET | `/api/heatmap` | Legacy heatmap grid (default dataset) |
| GET | `/api/status` | Background-work status |
| GET | `/api/clear-cache` | Flush project cache (datasource untouched) |

Datasource (`:9000`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/archive/channels` | Channel list (element_id → name) |
| GET | `/archive/subsystems` | Subsystem list |
| GET | `/archive/extent` | Global time range |
| GET | `/archive/eventhistory` | Paginated raw rows (`offset`/`limit`) |
| GET | `/archive/eventhistory/count` | Row count for a channel/range |
| GET | `/archive/eventhistory.csv` | Streamed CSV |
| GET | `/archive/transitions` | FSM state transitions |

Query example:

```bash
curl -s http://localhost:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_id": "default",
    "entity_ids": ["default:900000"],
    "measure_ids": ["value"],
    "range": {"start": 1524069595, "end": 1524070195},
    "resolution": {"strategy": "auto", "pixel_width": 1200, "points_per_pixel": 2},
    "downsampling": "M4"
  }' | python -m json.tool
```

Entity IDs are opaque — take them from `/api/datasets/{id}/entities` and never
parse them client-side. Nulls and gaps are evidence: missing samples are
absent rows or null buckets, never zero or forward-filled.

## Environment variables

```
DCSVIZ_DB           postgresql://dcs:dcs@localhost:5433/dcs  (project cache)
DCSVIZ_ARCHIVE      http://datasource:9000                   (default config base_url)
DCSVIZ_CONFIG_DIR   configs                                  (dataset config dir)
DCSVIZ_SRC_DB       postgresql://dcs:dcs@localhost:5434/dcs  (datasource, ingest)
```

Compose sets the first three for the containers; configs may reference them
with `${VAR}` / `${VAR:-default}`.

## Development

```bash
source .venv/bin/activate
pip install -e "./anaviz[test]"
pytest anaviz/tests/ -q               # offline unit tests, no DB needed
python3 -m compileall -q anaviz/server datasource/anaviz_datasource
for file in anaviz/frontend/*.js; do node --check "$file"; done
git diff --check
```

Tests cover config validation/fingerprinting, the configurable adapter
(extraction, pagination, gap preservation, matrix missingness), canonical
contracts, downsampling (LTTB / M4 / MinMaxLTTB), opt-in rollup tiers, and
the no-data-loss dense-range path.

After backend/frontend changes, rebuild the project (this never touches the
datasource volume):

```bash
make -C anaviz up            # rebuild + restart the project only
```

Hard-refresh the browser after rebuilding (`Ctrl+Shift+R`).

## Data-safety rules

| Action | Datasource | Project cache |
|--------|-----------|---------------|
| Rebuild / restart a project (`make -C … up`) | none | retained |
| `GET /api/clear-cache` | none | truncated, rehydrated on demand |
| `DELETE /api/configs/{id}` | none | only that dataset's namespace dropped |
| `make -C … down` (compose down) | none | volumes retained (external) |
| `docker volume rm anaviz_anaviz_datasource_pgdata` | **data deleted** | — |

Because both compose files bind their postgres volumes as **external**, a
normal `down` never removes data. Only explicitly running `docker volume rm`
on `anaviz_anaviz_datasource_pgdata` destroys the raw dataset and requires a full
re-ingest — only use it intentionally.

## Troubleshooting

- **Port already in use**: `sudo docker ps -a`, stop the stale container.
- **`/archive/extent` returns nulls**: data not ingested — run step 4.
- **App shows no data but datasource is fine**: the cache is empty for that
  range — query the range in the app (hydration is on demand). If stuck:
  `curl -s http://localhost:8000/api/clear-cache` and retry.
- **Config registration fails**: endpoints must expose the mapped fields,
  `series.parameters` must map `entity`/`start`/`end`, and `base_url` must be
  an `http(s)` URL without embedded credentials.
