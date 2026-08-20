# Anaviz — Presentation Cheat Sheet

> Quick-reference card for demo presentation. One-liners per section.

---

## 🎯 What is Anaviz?

**One-liner:** Dataset-independent time-series visualization — you bring your own HTTP data source + config, Anaviz adapts.

**Key differentiator:** Ships with ZERO preloaded data. Works with ANY time-series API, not just this one.

---

## 📊 The Dataset

| Fact | Detail |
|------|--------|
| **Name** | ATLAS HLT p-beat |
| **Source** | CERN's Large Hadron Collider (Zenodo 7908064) |
| **What** | Heartbeat monitoring rates from HLT computing nodes |
| **Values** | Hz (events/second) — very small, 0–0.60 Hz |
| **Channels** | 3,690 DCM nodes across 4 HLT subsystems |
| **Time** | April – October 2018 (~6 months) |
| **Sparse** | ~48% of channels are all-NaN (no data) |
| **Files** | 3 CSVs: train (2GB), test (1GB), val (632MB) |

**HLT p-beat = "the speedometer readings from the ATLAS trigger system"**

---

## 🏗️ Architecture

| Container | Port | What | Image |
|-----------|------|------|-------|
| **datasource** | :9000, :5434 | Real HLT data + /archive/* API | `anaviz-datasource` |
| **project** | :8000, :5433 | Generic viz app + cache | `anaviz-project` |

**They share NOTHING.** Different Dockerfiles, different packages, different codebases.

---

## 🔄 Data Flow (The Story to Tell)

```
1. anaviz-download → Download CSVs from Zenodo
2. anaviz-ingest   → Load into datasource TimescaleDB
3. /archive/*      → Datasource serves raw rows over HTTP
4. config.json     → Tells the project how to talk to the datasource
5. POST /api/query → Project hydrates its cache, downsamples, returns series
6. Frontend        → Renders chart + heatmap + histograms
```

**Talk through this during the demo:**
> "Notice that the project has no idea what HLT is. It only knows how to talk to an HTTP API because of the config.json. If I pointed it at Prometheus or InfluxDB, the same frontend would work."

---

## 🔧 config.json (The Bridge)

**Talk through this during the demo:**
> "This config.json is the bridge between the datasource and the project. It says:
> - WHERE to fetch data: `http://datasource:9000/archive/eventhistory`
> - HOW to parse the response: timestamp is at `t`, value at `value`
> - WHAT the response shape is: columnar arrays (parallel `t`, `value`, `quality_flag` arrays)"

**Key fields:**
```json
{
  "endpoints.series.path": "/archive/eventhistory",
  "endpoints.series.shape": "columnar",
  "mapping.timestamp": "t",
  "mapping.measures.value.value_path": "value",
  "rollup_levels": [1, 4, 16, 64, 256]
}
```

---

## 📈 Downsampling

| Algorithm | What | When |
|-----------|------|------|
| **M4** | First/min/max/last per bucket | Default — best for most data |
| **LTTB** | Largest-Triangle-Three-Buckets | Best for continuous smooth signals |
| **MINMAXLTTB** | LTTB + guaranteed min/max | When peaks/valleys matter |
| **RAW** | No downsampling | When you want exact data |

**Budget formula:** `pixel_width × points_per_pixel = max output points`
> "With 1000 pixels and 2 points per pixel, we downsample to at most 2000 points."

---

## 🛡️ Data Fidelity Guarantees

| Guarantee | How |
|-----------|-----|
| **No interpolation across gaps** | Gap markers in response → null in chart → line breaks |
| **No truncation** | Dense ranges → SQL bucket aggregation over ALL rows |
| **Quality preserved** | quality_flag propagated through pipeline |
| **Missing ≠ zero** | NaN/null upstream → absent in cache → null in chart |
| **Evidence layer** | Tooltip shows: observed/interpolated, sample count, quality |

**Talk through this during the demo:**
> "Notice the gap between 5000 and 5100 seconds — the line breaks there. We don't connect the dots across missing data. This is the 'evidence layer' — operators need to see where data is missing, not have it hidden."

---

## 🗄️ Database Design

| Table | Container | Purpose |
|-------|-----------|---------|
| `eventhistory` | datasource | Raw HLT time-series (TimescaleDB hypertable) |
| `hardware_mapping` | datasource | element_id → readable name |
| `cache_series` | project | Generic cache (shared across ALL datasets) |
| `cache_coverage` | project | Tracks which ranges are cached |
| `cache_rollup` | project | Opt-in bucket aggregation tiers |

**Key insight:** `cache_series` is shared. Isolation is by `dataset_key = "dataset_id:fingerprint"`.

---

## 🧪 Tests

```bash
pytest tests/ -q    # 72+ tests, no DB needed
```

| File | Tests |
|------|-------|
| `test_config.py` | Config validation, env substitution, fingerprints |
| `test_configurable.py` | HTTP extraction, pagination, gap preservation |
| `test_rollup.py` | Tier selection, bucket aggregation, backfill |
| `test_downsample.py` | LTTB/M4/MINMAXLTTB correctness |
| `test_canonical_contract.py` | Pydantic model compliance |
| `test_data_fidelity.py` | No-data-loss guarantees |

---

## 💻 Commands to Demo

```bash
# Start the stack
sudo docker compose up -d --build

# Open browser
# http://localhost:8000

# Check datasource is healthy
curl http://localhost:9000/archive/extent
# {"t_min": 1524076795.002, "t_max": 1539884215.999}

# Check project is healthy
curl http://localhost:8000/api/datasets

# Query data
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_id": "default",
    "entity_ids": ["default:900000"],
    "measure_ids": ["value"],
    "range": {"start": 1524076795, "end": 1524163195},
    "resolution": {"pixel_width": 800, "points_per_pixel": 2},
    "downsampling": "M4"
  }'
```

---

## 🔑 Key Talking Points

1. **"This is real CERN data, not simulated"** — from Zenodo, actual HLT monitoring rates
2. **"The project knows nothing about HLT"** — it's dataset-independent by design
3. **"config.json is the bridge"** — one config file connects any HTTP API
4. **"We never interpolate across gaps"** — evidence layer preserves data quality
5. **"Dense ranges never truncate"** — SQL bucket aggregation over ALL rows
6. **"Two containers, zero shared code"** — clean separation of concerns
7. **"72+ tests, no DB needed"** — all unit tests are offline

---

## ❓ Anticipated Questions

**Q: Why not use Grafana?**
> Grafana needs a specific datasource plugin for each API. Anaviz works with ANY HTTP API via config.json — no plugin development needed.

**Q: What about real-time updates?**
> The current system is pull-based (query on demand). Real-time could be added via WebSocket on the same adapter pattern.

**Q: How does this scale?**
> Rollup tiers + pixel-budget downsampling. A 6-month dataset gets aggregated to 16s buckets before downsampling to ~2000 points.

**Q: Can it handle other data types?**
> Yes — the config.json supports any HTTP API returning timestamps + values. Temperature, voltage, event rates, etc.
