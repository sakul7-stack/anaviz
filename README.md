# Anaviz

Dataset-independent time-series visualization. Bring your own HTTP data
source + `config.json`; Anaviz caches data on demand and serves a generic
frontend (chart, heatmap, histogram).

## Quick start

```bash
# 1. Start both (datasource :9000 + project :8000)
make up

# 2. Register the bundled HLT config (or paste in Data Sources panel)
curl -X POST http://localhost:8000/api/configs \
  -H 'Content-Type: application/json' \
  -d "{\"config\": $(cat example.json)}"

# 3. Open http://localhost:8000
```

## Ports

| Service    | App  | DB    | Description               |
|------------|------|-------|---------------------------|
| datasource | :9000 | :5434 | HLT data API              |
| project    | :8000 | :5433 | Viz app + cache           |

## Architecture

Two independent containers on a shared Docker network. The project
hydrates its cache over HTTP — `clear-cache` never destroys real data.

```
datasource (:9000)  ──HTTP──▶  project (:8000)
real HLT data                    viz app + generic cache
```

## Docs

- [`anaviz/`](anaviz/README.md) — project (viz app, config, API, architecture)
- [`datasource/`](datasource/README.md) — HLT datasource (download, ingest, schema)

## Development

```bash
pip install -e "./anaviz[test]"
pytest anaviz/tests/ -q
python3 -m compileall -q anaviz/server
for f in anaviz/frontend/*.js; do node --check "$f"; done
```
