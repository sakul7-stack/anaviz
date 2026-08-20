# Dataset configs

This directory is **empty on purpose**. The project ships with no preloaded
dataset — you register your own data source (API URL + `config.json`) through
the **Data sources** panel in the UI at http://localhost:8000, or via
`POST /api/configs`.

Optionally, you can also drop a `*.json` config file here and restart the
project; every `*.json` in this directory is loaded at startup
(`DCSVIZ_CONFIG_DIR`, mounted read-only into the project container).

See the README at the repository root for the `config.json` schema.
