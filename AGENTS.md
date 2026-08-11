# AGENTS.md — anaviz time-series evidence explorer

Dataset-independent time-series visualization: users bring their own HTTP data
source plus a `config.json` schema, and Anaviz adapts to it. The canonical
boundary is defined in `server/api/contracts.py`; source-specific IDs and
schemas are translated by a `ConfigurableHttpAdapter` built from the validated
config — never embedded in frontend renderers.

The 2-container architecture remains, with **two fully independent images**:
the **datasource** image (`datasource/Dockerfile`, a standalone mini-project
with its own `pyproject.toml`, entrypoint, and CLI) contains only the
`anaviz_datasource` package — real HLT data + `/archive/*` on :9000; the
**project** image (`docker/Dockerfile.project`) contains only `server/`,
`frontend/`, and `configs/` — the dataset-independent viz app whose generic
cache (`cache_series`, `cache_coverage`) is hydrated over HTTP, so
`clear-cache` can never destroy the real data. The images share nothing except
a similar entrypoint. Never add HLT code to the project image or project code
to the datasource image.

## Layout

```
server/          FastAPI composition (`app.py`) + archive datasource server
server/api/      canonical contracts, config model, configurable HTTP adapter
server/downsample/ M4, LTTB, MinMaxLTTB downsampling
frontend/        Vanilla JS + uPlot/Canvas renderers consuming canonical APIs
configs/         user-supplied *.json dataset configs; intentionally EMPTY in
                 the repo — nothing is preloaded. Register via the UI or
                 POST /api/configs, or drop a config here and restart.
datasource/      STANDALONE HLT datasource mini-project: its own pyproject.toml,
                 Dockerfile, entrypoint.sh, and CLI (anaviz-ingest /
                 anaviz-download). Never imported or packaged by the project.
db/              generic cache schema (project only)
data/hlt/        downloaded CSVs (gitignored)
docker/          project image only: Dockerfile.project + entrypoint.sh
                 (the datasource has its own Dockerfile + entrypoint inside
                 datasource/)
docs/            reference papers (read-only)
```

## Core commands

```bash
sudo docker compose up -d --build                     # both containers
sudo docker compose up -d --build datasource          # datasource only (:9000, db :5434)
sudo docker compose up -d --build project             # project only (:8000, cache :5433)
make db-up && make app-up                             # or the Makefile targets
pip install -e ".[test]"                              # project host tooling
pip install -e ./datasource                           # standalone HLT tooling
anaviz-download                                       # resumable; re-run to resume
anaviz-ingest --data-dir data/hlt --files all \
  --db-url postgresql://dcs:dcs@localhost:5434/dcs    # ingest INTO THE DATASOURCE DB
# → open http://localhost:8000
#   datasource API: http://localhost:9000/archive/*
#   project cache psql: sudo docker compose exec project psql -U dcs -d dcs
#   datasource psql:    sudo docker compose exec datasource psql -U dcs -d dcs
```

Tests: `pytest tests/ -q` — 48 offline unit tests for config validation,
fingerprinting, the configurable adapter (extraction, pagination, gap
preservation, matrix missingness), canonical contracts, downsampling (LTTB /
M4 / MINMAXLTTB band correctness), and data fidelity. No DB needed. Add a
`tests/test_api.py` integration suite when the stack is up.

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
  datasets, entities, measures, dimensions, series points, and typed evidence
  only.
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
  and provenance generation in `server/api/configurable.py`; `server/app.py`
  contains composition and routes only.
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
- Verify with `python3 -m compileall server datasource/anaviz_datasource` and
  `for f in frontend/*.js; do node --check "$f"; done` after edits.

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

- The generic cache tables (`cache_series`, `cache_coverage`) are created by
  `db/cache_schema.sql` on first boot AND by `GenericDatasetCache.ensure_tables`
  at app startup. They are shared by every dataset; isolation is by
  `dataset_key = "<dataset_id>:<config_fingerprint>"`.
- The datasource container uses the HLT schema (`datasource/schema.sql`); the
  project container mounts the generic cache schema (`db/cache_schema.sql`)
  instead. Do not mount HLT rollups into the project.
- `datasource/anaviz_datasource/ingest_hlt.py` maps CSV columns to
  `element_id = 900_000 + index`. `hardware_mapping.full_name` is derived from
  the DCM node column names.
- The HLT CSVs are big (~2 GB train, ~1 GB test, ~632 MB val). Ingest with
  `--files all` only when all three are fully downloaded (verify with
  `anaviz-download --check`).
- Both containers run postgres + uvicorn via `docker/entrypoint.sh`; init
  scripts run on first boot of an empty volume.
- The datasource container must be re-ingested after its volume is wiped; the
  project cache re-hydrates from :9000 automatically.
