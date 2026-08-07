# ATLAS DCS Explorer

Large-scale time-series visualization for ATLAS Detector Control System data,
built on real ATLAS HLT p-beat data (Zenodo record 7908064).

## Quick start

### First time running this project

```bash
# 1. Install host tooling
pip install -e ".[hlt]"

# 2. Download the HLT data (~3.6 GB) into data/hlt/
#    From Google Drive: https://drive.google.com/drive/folders/16ZZ-gTyvSyQi01OgaL0vRa0TDfQxlRNF?usp=sharing
#    Put hlt_train_set.csv, hlt_test_set.csv, hlt_val_set.csv into data/hlt/
#    (or run `python scripts/download_hlt.py` if you prefer the Zenodo download)
python scripts/download_hlt.py --check     # verify the files are complete

# 3. Build and start both containers
sudo docker compose up -d --build

# 4. Ingest the CSVs into the DATASOURCE DB (one-time, takes a while)
python -m server ingest --data-dir data/hlt --files all \
  --db-url postgresql://dcs:dcs@localhost:5434/dcs

# 5. Open the app
http://localhost:8000
```

### Running it again

```bash
sudo docker compose up -d     # start both containers (already built, already ingested)
# → http://localhost:8000
```

That's it — the data stays in the datasource container's volume, so you never
need to download or ingest again.

## Architecture

Two containers, both running TimescaleDB + a FastAPI service:

- **datasource** (`:9000`, postgres on host `:5434`) — owns the real HLT data
  and serves it through an `/archive/*` API. Source of truth; never truncated.
- **project** (`:8000`, postgres on host `:5433`) — the viz app with its own
  cache DB. Its `eventhistory` is hydrated over HTTP from the datasource, so
  `/api/clear-cache` can never destroy the real data.

Rollups are `WITH NO DATA` and refreshed **on demand** — nothing is precomputed.

```
┌───────────────────────────┐         ┌───────────────────────────┐
│  datasource container     │  :9000  │  project container        │
│  real HLT data (Timescale)│◄────────│  viz app + cache DB       │
│  /archive/* API           │  HTTP   │  /api/* (uPlot SPA)       │
│  :5434 (host, ingest)     │         │  :5433 (host, cache)      │
└───────────────────────────┘         └───────────────────────────┘
```

| Service    | HTTP        | Postgres (host) | Volume                    |
|------------|-------------|-----------------|---------------------------|
| datasource | `:9000`     | `:5434`         | `anaviz_datasource_pgdata`|
| project    | `:8000`     | `:5433`         | `anaviz_cache_pgdata`     |

## Features

- Resolution-aware query engine (raw → 1m → 5m → 1h → 1d rollups)
- Three downsampling algorithms: M4, LTTB, MinMaxLTTB
- SPOT anomaly scoring panel (streaming extreme-value theory)
- Cross-channel z-score heatmap
- Scroll-wheel zoom, drag pan, hover tooltip
- Cache-aside hydration with read-ahead warming

## Prerequisites

- Docker Engine with `docker compose` (v2). May need `sudo`.
- Python 3.11+ on the host (for download + ingest tooling).
- ~6 GB free disk for the CSVs, plus room for the DB volumes.

## First-run details

### Downloading the HLT data

The Zenodo download can be slow or fail; the data is also mirrored on Google
Drive. Download the folder into `data/hlt/`:

**https://drive.google.com/drive/folders/16ZZ-gTyvSyQi01OgaL0vRa0TDfQxlRNF?usp=sharing**

Put the three CSVs in `data/hlt/` (resumable re-runs of `download_hlt.py` work
only for the Zenodo path; for Drive just re-download any missing file):

- `hlt_train_set.csv` (~2 GB)
- `hlt_test_set.csv`  (~995 MB)
- `hlt_val_set.csv`   (~632 MB)

Verify completeness before ingesting:

```bash
python scripts/download_hlt.py --check
```

### Building the stack

```bash
sudo docker compose up -d --build
```

Builds one image, starts both containers. The project container waits until
the datasource is healthy before starting. Check status/logs with:

```bash
sudo docker compose ps
sudo docker compose logs -f datasource     # or project
```

### Ingesting the data

Ingests the CSVs into the **datasource** DB (host port `:5434`). Never ingest
into the project cache (`:5433`).

```bash
python -m server ingest --data-dir data/hlt --files all \
  --db-url postgresql://dcs:dcs@localhost:5434/dcs
```

- `--files train`      → train only (quickest way to see data)
- `--files train+test` → default
- `--files all`        → train + test + val

Ingest is resumable and idempotent — if interrupted, just re-run it.

### Verifying the datasource

```bash
curl -s http://localhost:9000/archive/extent       # → {"t_min":..., "t_max":...}
curl -s http://localhost:9000/archive/channels | head
```

Non-null `t_min`/`t_max` means the data is served correctly.

### Using the app

Open http://localhost:8000, pick a channel and a time range. The project
hydrates its cache from `:9000` on demand — the first query for a range is
slower while it fetches; later ones come from the cache.

Use `/api/clear-cache` freely — it only flushes the project cache; the
datasource keeps the real data.

## API endpoints

Project (`:8000`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/extent` | Global time range |
| GET | `/api/channels` | Channel list |
| GET | `/api/series` | Downsampled time-series (multi-channel) |
| GET | `/api/transitions` | FSM state transitions |
| GET | `/api/anomaly-score` | SPOT per-timestep anomaly scores |
| GET | `/api/heatmap` | Z-score cross-channel heatmap grid |
| GET | `/api/clear-cache` | Flush local cache (safe — datasource untouched) |

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

## Environment variables

```
DCSVIZ_DB        postgresql://dcs:dcs@localhost:5433/dcs  (project cache)
DCSVIZ_ARCHIVE   http://datasource:9000                   (inside compose)
DCSVIZ_SRC_DB    postgresql://dcs:dcs@localhost:5434/dcs  (datasource, ingest)
DATA_SOURCE      http                                     (http | hlt | simulator)
```

Compose sets these automatically. `DATA_SOURCE=http` uses the 2-container
`/archive/*` path; `hlt` reads a local DB directly (legacy workflow).

## Project layout

```
server/          FastAPI backend (app.py, archive_server.py, api/*, downsample/*)
frontend/        Vanilla JS + uPlot SPA (app.js, chart.js, heatmap.js, utils.js)
db/              TimescaleDB init SQL (db/init/002_schema.sql) + rollups (db/rollups_init.sql)
scripts/         data pipeline helpers (download_hlt.py)
data/hlt/        downloaded CSVs (gitignored)
docker/          container build (Dockerfile, entrypoint.sh)
docs/            reference papers (CERN PDFs, read-only)
```

## Tests

```bash
pytest tests/ -q    # 13 offline unit tests (downsampling + resolution tiers), no DB needed
```

## Troubleshooting

- **Port already in use**: `sudo docker ps -a`, stop/kill the stale container,
  then `sudo docker compose up -d`.
- **`/archive/extent` returns nulls**: data not ingested — run the ingest
  command from First-time setup.
- **App shows no data but datasource is fine**: cache is empty for that range —
  query the range in the app (hydration is on demand). If stuck:
  `curl -s "http://localhost:8000/api/clear-cache"` and retry.
- **Need a clean slate**: `sudo docker compose down -v`, then re-ingest
  (`--db-url ...:5434/dcs`).

## Overlay toggles: what they actually do

### SPOT score panel

Runs SPOT (Streaming Peak Over Threshold, Siffer et al. 2017) on the **first
selected channel only**, for the current range. It fits a Generalized Pareto
Distribution to the top ~2% of a 1-hour training window *before* the range,
then scores each visible point in [0, 1] (1 = most anomalous) and draws the
score curve + the fitted threshold line in a small panel under the chart.

Two things to know with real HLT data:

- It flags `value > threshold` (raw units), so the panel hint
  `(score > threshold → anomalous)` is misleading — points above the threshold
  are the anomalies, and the score just measures how far out in the tail they are.
- Real DCM channels are smooth, mostly-normal telemetry with a bounded range
  (e.g. 0–3). The 1-in-10000 calibrated threshold typically lands *above the
  channel max*, so **zero points ever cross it** and the panel renders a flat
  score line at 0 — i.e. "no anomalies found", which is the expected (boring)
  answer for healthy data. The threshold line itself is drawn on the clamped
  [0, 1] score axis and usually ends up off-screen, so you won't see it either.

