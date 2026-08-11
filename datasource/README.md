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
```

## Commands

```bash
pip install -e .                     # installs anaviz-ingest / anaviz-download

anaviz-download --check              # verify the CSVs are complete
anaviz-download                      # download all three CSVs (resumable)
anaviz-ingest --data-dir data/hlt --files all \
  --db-url postgresql://dcs:dcs@localhost:5434/dcs

python -m anaviz_datasource ingest --data-dir data/hlt   # same, as a module
```

## Docker

Build and run the datasource on its own (no project needed):

```bash
docker build -t anaviz-datasource:latest .
docker run --rm -p 9000:9000 -p 5434:5432 \
  -e POSTGRES_USER=dcs -e POSTGRES_PASSWORD=dcs -e POSTGRES_DB=dcs \
  -e DCSVIZ_DB=postgresql://dcs:dcs@localhost:5432/dcs \
  -v anaviz_datasource_pgdata:/var/lib/postgresql/data \
  -v $PWD/schema.sql:/docker-entrypoint-initdb.d/002_schema.sql:ro \
  anaviz-datasource:latest \
  uvicorn --app-dir /app anaviz_datasource.archive_server:app \
    --host 0.0.0.0 --port 9000
```

Verify: `curl http://localhost:9000/archive/extent`.

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
