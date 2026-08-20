# anaviz-datasource — independent HLT data API

A self-contained package that owns the real ATLAS HLT p-beat dataset
(Zenodo record 7908064) and serves it over an `/archive/*` HTTP API. It is
**fully independent** of the anaviz project: it has its own package metadata,
CLI, Docker image, and entrypoint. Nothing is preloaded in the project — a
user registers this API (URL + config.json) in the app's Data sources panel,
just like any other user-supplied source.

```
anaviz_datasource/
  archive_server.py   FastAPI app exposing /archive/* on :9000
  ingest_hlt.py       resumable, conflict-safe CSV ingestion
  download_hlt.py     resumable Zenodo download/check helper
schema.sql            HLT TimescaleDB schema (eventhistory, hardware_mapping, ...)
data/hlt/             the downloaded CSVs (gitignored) — owned by this project
docker-compose.yml    standalone stack (compose project: anaviz-datasource)
Dockerfile            the datasource image; entrypoint.sh; Makefile; .dockerignore
```

This project is completely standalone: run everything from **inside this
folder**. Its postgres data volume is declared **external** in
`docker-compose.yml`, bound to the existing physical volume
`anaviz_anaviz_datasource_pgdata`, so bringing it up **reuses your ingested
data and never re-ingests**.

## Commands

Run from inside `datasource/`:

```bash
pip install -e .                     # installs anaviz-ingest / anaviz-download

make download                        # = python -m anaviz_datasource download --out data/hlt
python -m anaviz_datasource download --check   # verify the CSVs are complete
make ingest                          # ingest data/hlt INTO THE DATASOURCE DB (:5434)
                                     # = python -m anaviz_datasource ingest --data-dir data/hlt \
                                     #     --files all --db-url postgresql://dcs:dcs@localhost:5434/dcs
```

## Docker

Bring up the datasource on its own (no project needed). This uses the external
data volume, so it reuses your already-ingested data:

```bash
make up            # docker network anaviz_shared + compose up -d --build
# or, equivalently, from this folder:
docker network create anaviz_shared 2>/dev/null || true
docker compose up -d --build

curl http://localhost:9000/archive/extent    # → {"t_min":..., "t_max":...}
make down          # stop (data volume retained)
```

> **Data-safety note.** The compose file binds the postgres volume as
> `external` → `anaviz_anaviz_datasource_pgdata` (your existing data). A plain
> `docker run -v anaviz_datasource_pgdata:...` would create a *different*,
> empty volume and force a full re-ingest — use the compose file / `make up`
> instead. Only `docker volume rm anaviz_anaviz_datasource_pgdata` deletes the
> real data.

The API contract is stable and generic:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/archive/channels` | Channel list (element_id → name) |
| GET | `/archive/subsystems` | Subsystem list |
| GET | `/archive/extent` | Global time range |
| GET | `/archive/eventhistory` | Paginated raw rows (`offset`/`limit`) |
| GET | `/archive/eventhistory/count` | Row count for a channel/range |
| GET | `/archive/eventhistory.csv` | Streamed CSV for a channel/range |
| GET | `/archive/transitions` | FSM state transitions |
