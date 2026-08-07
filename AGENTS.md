# AGENTS.md — anaviz (ATLAS DCS Explorer)

Large-scale time-series visualization for real ATLAS HLT p-beat data
(Zenodo record 7908064). Rework of the `/home/kushal/Documents/CERN` simulator
prototype, using the same 2-container architecture: a **datasource** container
owns the real data and serves `/archive/*` on :9000; the **project** container
is the viz app whose `eventhistory` is only a cache hydrated over HTTP, so
`clear-cache` can never destroy the real data.

## Layout

```
server/          FastAPI backend (app.py, archive_server.py, api/*, downsample/*)
frontend/        Vanilla JS + uPlot SPA (app.js, chart.js, heatmap.js, utils.js)
db/              TimescaleDB init SQL (db/init/002_schema.sql) + on-demand rollups
                 (db/rollups_init.sql)
scripts/         data pipeline helpers (download_hlt.py)
data/hlt/        downloaded CSVs (gitignored)
docker/          container build (Dockerfile, entrypoint.sh)
docs/            reference papers (CERN PDFs, read-only)
```

## Core commands

```bash
sudo docker compose up -d --build                     # datasource (:9000, db :5434) + project (:8000, cache :5433)
pip install -e ".[hlt]"                               # host tooling for ingest
python scripts/download_hlt.py                        # resumable; re-run to resume
python -m server ingest --data-dir data/hlt --files all \
  --db-url postgresql://dcs:dcs@localhost:5434/dcs    # ingest INTO THE DATASOURCE DB
# → open http://localhost:8000
#   datasource API: http://localhost:9000/archive/*
#   project cache psql: sudo docker compose exec project psql -U dcs -d dcs
#   datasource psql:    sudo docker compose exec datasource psql -U dcs -d dcs
```

Tests: `pytest tests/ -q` — 13 offline unit tests for downsampling (LTTB / M4 /
MINMAXLTTB band correctness) and resolution-tier selection. Ported from the
CERN prototype; no DB needed. Add an `tests/test_api.py` integration suite
when the stack is up.

## Rules

- Only modify code inside `/home/kushal/Documents/anaviz`. The `CERN/` and
  `Docs/` folders are read-only references.
- This project tracks real CERN data. Never commit CSV/HDF5 files, `.env`,
  `anaviz.egg-info/`, `__pycache__/`, or the downloaded `data/` tree.
- Reuse the downsample/cache/resolution modules already present; don't
  re-implement them.
- Data pipeline must be resumable: any interrupted ingestion/download can be
  re-run without corrupting the DB. `download_hlt.py` resumes via HTTP Range;
  `ingest_hlt.py` uses `ON CONFLICT` for channels and chunked COPY.
- If the DATASOURCE DB has old partial rows from an interrupted ingest, wipe
  the tables first: `TRUNCATE eventhistory, hardware_mapping, cache_coverage CASCADE;`
  then re-ingest. Do NOT drop/recreate the hypertable needlessly.
  (The project cache DB may be wiped freely — it re-hydrates from :9000.)
- Never ingest into the project cache DB; it is hydrated on demand.
- Rollups are refreshed ON DEMAND by `server/api/cache.py` — do not add refresh
  policies or a manual full-refresh step.
- Verify with `python3 -m compileall server scripts` after edits.

## Environment

```
DCSVIZ_DB=postgresql://dcs:dcs@localhost:5433/dcs   # project cache DB
DCSVIZ_ARCHIVE=http://datasource:9000               # datasource /archive/* API
DCSVIZ_SRC_DB=postgresql://dcs:dcs@localhost:5434/dcs   # datasource DB (ingest)
DATA_SOURCE=http                                    # http (default) | hlt | simulator
```

## Gotchas

- Rollup views are `WITH NO DATA` and refreshed on demand — do NOT precompute
  or add continuous-aggregate policies.
- `ingest_hlt.py` maps CSV columns to `element_id = 900_000 + index`.
  `hardware_mapping.full_name` is derived from the DCM node column names.
- The HLT CSVs are big (~2 GB train, ~1 GB test, ~632 MB val). Ingest with
  `--files all` only when all three are fully downloaded (verify with
  `python scripts/download_hlt.py --check`).
- Both containers run postgres + uvicorn via `docker/entrypoint.sh`; init
  scripts (`db/init/002_schema.sql`, plus `db/rollups_init.sql` for the project
  cache) run on first boot of an empty volume.
- The datasource container must be re-ingested after its volume is wiped; the
  project cache re-hydrates from :9000 automatically.
