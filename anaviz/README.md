# Anaviz — project (visualization app)

This folder contains the **dataset-independent visualization project**: the
generic FastAPI backend, the vanilla-JS frontend, dataset configs, and the
generic cache schema. It contains **zero HLT-specific code** — it talks to any
data source only over HTTP, using a user-supplied `config.json`.

```
anaviz/
├── docker-compose.yml standalone stack (compose project: anaviz-project, :8000)
├── Dockerfile         project image; entrypoint.sh; Makefile; .dockerignore
├── pyproject.toml     the `anaviz` (server) package
├── server/            FastAPI app, canonical contracts, configurable adapter
├── frontend/          uPlot chart, heatmap, histograms (static assets)
├── configs/           user *.json dataset configs (empty by default)
├── db/                generic cache schema (cache_series/coverage/rollup)
└── tests/             offline unit tests for this project
```

The **datasource** (real HLT data + `/archive/*` on :9000) is a separate,
self-contained project in the repository-root `datasource/` folder and has its
own Dockerfile, compose, entrypoint, and package. The two share nothing.

## Run

This project is standalone. Run everything from **inside this folder**. Its
cache volume is declared **external** in `docker-compose.yml`, so bringing it
up reuses the existing cache (and it re-hydrates on demand anyway).

```bash
make up            # docker network anaviz_shared + compose up -d --build
# or, equivalently, from this folder:
docker network create anaviz_shared 2>/dev/null || true
docker compose up -d --build
# open http://localhost:8000
make down          # stop (cache volume retained)
```

To use the bundled HLT datasource, start that separate project too
(`make -C ../datasource up`) and register a config whose `base_url` is
`http://datasource:9000` (resolved over the shared `anaviz_shared` network).
The app boots empty and works fine even with no datasource running.

## Develop this project only

```bash
pip install -e .            # from inside anaviz/ (installs the server package)
pip install -e ".[test]"
make test                   # = python -m pytest tests/ -q  (offline, no DB)
python3 -m compileall server
for f in frontend/*.js; do node --check "$f"; done
```

See the repository-root `README.md` for the full architecture, the
`config.json` schema, the API reference, and data-safety rules.
