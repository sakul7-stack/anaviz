# AGENTS.md — anaviz time-series explorer

Dataset-independent time-series visualization: users bring their own HTTP data
source plus a `config.json` schema, and Anaviz adapts to it. The canonical
boundary is defined in `server/api/contracts.py`; source-specific IDs and
schemas are translated by a `ConfigurableHttpAdapter` built from the validated
config — never embedded in frontend renderers.

The 2-container architecture remains, with **two fully independent images**:
the **datasource** image (`datasource/Dockerfile`, a standalone mini-project
with its own `pyproject.toml`, entrypoint, and CLI) contains only the
`anaviz_datasource` package — real HLT data + `/archive/*` on :9000; the
**project** image (`anaviz/Dockerfile`) contains only `anaviz/server/`,
`anaviz/frontend/`, and `anaviz/configs/` — the dataset-independent viz app
whose generic cache (`cache_series`, `cache_coverage`) is hydrated over HTTP,
so `clear-cache` can never destroy the real data. The images share nothing
except a similar entrypoint. Never add HLT code to the project image or
project code to the datasource image.

## Layout

```
anaviz/          the visualization PROJECT — standalone (compose project: anaviz-project, :8000)
  docker-compose.yml  external cache volume + shared net; app :8000, cache DB :5433
  Dockerfile     project image (context ./anaviz); entrypoint.sh; pyproject.toml
  Makefile       per-project targets (up/down/logs/psql/test)
  server/        FastAPI composition (`app.py`) + routes/composition only
  server/api/    canonical contracts, config model, configurable HTTP adapter
  server/downsample/ M4, LTTB, MinMaxLTTB downsampling + rollup tiers (rollup.py)
  frontend/      Vanilla JS + uPlot/Canvas renderers consuming canonical APIs
  configs/       user-supplied *.json dataset configs; intentionally EMPTY in
                 the repo — nothing is preloaded. Register via the UI or
                 POST /api/configs, or drop a config here and restart.
  db/            generic cache schema (project only)
  tests/         offline unit tests for the project (import `server.*`)
datasource/      STANDALONE HLT datasource project (compose project: anaviz-datasource, :9000)
  docker-compose.yml  external data volume + shared net; API :9000, DB :5434
  Dockerfile, entrypoint.sh, Makefile, pyproject.toml, schema.sql
  anaviz_datasource/  package + CLI (anaviz-ingest / anaviz-download)
  data/hlt/      downloaded HLT CSVs (gitignored) — owned by the datasource
docs/            reference papers (read-only)
  demo/          presentation material (ODT/PPTX generators, images) — not runtime
Makefile         root convenience only: `make up` starts both on anaviz_shared net
```

There is NO root docker-compose.yml. Each project has its own compose file and
binds its postgres volume as **external** to the existing physical volume
(`anaviz_anaviz_datasource_pgdata`, `anaviz_anaviz_cache_pgdata`), so `up`/`down`
never re-ingest. The two projects find each other over the external
`anaviz_shared` Docker network (datasource reachable as `http://datasource:9000`).

## Core commands

```bash
make up                                               # both projects (net + datasource + anaviz)
make -C datasource up                                 # datasource only (:9000, db :5434)
make -C anaviz up                                     # project only (:8000, cache :5433)
# each folder is its own compose project: `cd anaviz && docker compose up -d` also works
pip install -e "./anaviz[test]"                       # project host tooling
pip install -e ./datasource                           # standalone HLT tooling
cd datasource && python -m anaviz_datasource download --out data/hlt   # resumable
cd datasource && make ingest                          # ingest data/hlt INTO THE DATASOURCE DB (:5434)
# → open http://localhost:8000
#   datasource API: http://localhost:9000/archive/*
#   project cache psql: docker compose -f anaviz/docker-compose.yml exec project psql -U dcs -d dcs
#   datasource psql:    docker compose -f datasource/docker-compose.yml exec datasource psql -U dcs -d dcs
```

Tests: `pytest anaviz/tests/ -q` — 72 offline unit tests for config validation,
fingerprinting, the configurable adapter (extraction, pagination, gap
preservation, matrix missingness), canonical contracts, downsampling (LTTB /
M4 / MINMAXLTTB band correctness), rollup tier selection + bucket
aggregation, and data fidelity. No DB needed. Add a `tests/test_api.py`
integration suite when the stack is up.

## Rules

- Only modify code inside `/home/kushal/Documents/anaviz`. The `CERN/` and
  `Docs/` folders are read-only references.
- `datasource/` is a standalone mini-project: it has its own pyproject.toml,
  Dockerfile, entrypoint.sh, and CLI. Never add HLT knowledge to `server/`,
  `frontend/`, or `configs/`; never import `anaviz_datasource` from the
  project; never add the datasource package to the root `pyproject.toml`. The
  project talks to the datasource only through the generic `/archive/*` API;
  a user registers it with their own config.json. Never ship a preloaded
  config in `configs/` — the app must boot with zero datasets.
- Keep source-specific identifiers, database columns, and naming rules inside a
  config + `ConfigurableHttpAdapter`. Canonical APIs and frontend renderers use
  datasets, entities, measures, dimensions, and series points only.
- A user `config.json` describes endpoints, field mappings, pagination, and
  measures. Configs are declarative data only — no code, SQL, or templates.
  Never execute anything parsed from a config.
- Config changes create a new cache namespace via the config fingerprint. Never
  truncate another dataset's rows; `DELETE /api/configs/{id}` drops only that
  dataset's namespace.
- Preserve `/api/channels`, `/api/series`, `/api/extent`, `/api/heatmap` as
  compatibility facades over the default adapter while new visualization
  behavior uses `/api/datasets/*`, `/api/configs`, and `POST /api/query`.
- Keep HTTP fetching, cache SQL, rollup/downsampling decisions, coverage math,
  the no-data-loss SQL bucket aggregation (`read_bucketed`), and provenance
  generation in `server/api/configurable.py`; `server/app.py` contains
  composition and routes only.
- Never truncate a visualization: dense raw ranges (more cached rows than
  `row_cap`) are aggregated in Postgres over ALL rows into time buckets sized
  to the pixel budget (exact first/min/max/last per bucket), never cut off at
  the cap. The response marks `aggregated` + `query_aggregated`.
- Reuse the downsample modules and `server/api/common.py` helpers already
  present; don't re-implement them.
- Data pipeline must be resumable: any interrupted ingestion/download can be
  re-run without corrupting the DB. `anaviz_datasource/download_hlt.py`
  resumes via HTTP Range; `anaviz_datasource/ingest_hlt.py` uses `ON CONFLICT`
  for channels and chunked COPY.
- If the DATASOURCE DB has old partial rows from an interrupted ingest, the
  ingestion command refuses to guess when duplicate `(element_id, ts)` rows
  already exist. Repair or recreate that datasource volume before re-ingesting.
  The project cache may be wiped freely — it re-hydrates from :9000.
- Never ingest into the project cache DB; it is hydrated on demand from the
  configured source.
- Verify with `python3 -m compileall anaviz/server datasource/anaviz_datasource`
  and `for f in anaviz/frontend/*.js; do node --check "$f"; done` after edits.

## Environment

```
DCSVIZ_DB=postgresql://dcs:dcs@localhost:5433/dcs   # project cache DB
DCSVIZ_ARCHIVE=http://datasource:9000               # datasource /archive/* API
DCSVIZ_CONFIG_DIR=configs                           # dataset config directory
DCSVIZ_SRC_DB=postgresql://dcs:dcs@localhost:5434/dcs   # datasource DB (ingest)
```

`DCSVIZ_ARCHIVE` (default `http://datasource:9000` inside Compose) is what a
user config points `base_url` at to register the datasource in the app. No
config ships with the repo.

## Gotchas

- The generic cache tables (`cache_series`, `cache_coverage`, `cache_rollup`)
  are created by `db/cache_schema.sql` on first boot AND by
  `GenericDatasetCache.ensure_tables` at app startup. They are shared by every
  dataset; isolation is by `dataset_key = "<dataset_id>:<config_fingerprint>"`.
  `cache_rollup` holds opt-in bucket tiers (first/min/max/last/avg/count)
  aggregated from raw rows at hydration time. Rollups are strictly opt-in:
  declaring `rollup_levels=[...]` bucket sizes (seconds) builds exactly those
  tiers; `rollup_enabled: false` kill-switches them even if levels are
  declared. Nothing is derived from the source's sampling step — without
  `rollup_levels`, every range is served from raw rows.
- The datasource container uses the HLT schema (`datasource/schema.sql`); the
  project container mounts the generic cache schema (`db/cache_schema.sql`)
  instead. Do not mount HLT rollups into the project.
- `datasource/anaviz_datasource/ingest_hlt.py` maps CSV columns to
  `element_id = 900_000 + index`. `hardware_mapping.full_name` is derived from
  the DCM node column names.
- The HLT CSVs are big (~2 GB train, ~1 GB test, ~632 MB val). Ingest with
  `--files all` only when all three are fully downloaded (verify with
  `anaviz-download --check`).
- Both containers run postgres + uvicorn via their own `entrypoint.sh`
  (`anaviz/entrypoint.sh` and `datasource/entrypoint.sh`); init scripts run on
  first boot of an empty volume.
- The datasource container must be re-ingested after its volume is wiped; the
  project cache re-hydrates from :9000 automatically.
