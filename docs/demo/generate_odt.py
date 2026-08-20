#!/usr/bin/env python3
"""Generate Anaviz_Presentation.odt with embedded images."""

import os
from odf.opendocument import OpenDocumentText
from odf.style import Style, TextProperties, ParagraphProperties
from odf.text import H, P, Span
from odf.draw import Image

IMG_DIR = os.path.join(os.path.dirname(__file__), "images")

doc = OpenDocumentText()
styles = doc.styles

# ── Create styles ───────────────────────────────────────────────────────────
for name, fs, fw in [("Title", "24pt", "bold"), ("H1", "18pt", "bold"),
                      ("H2", "14pt", "bold"), ("H3", "12pt", "bold"),
                      ("Body", "11pt", "normal"), ("Code", "9pt", "normal"),
                      ("Caption", "9pt", "normal")]:
    st = Style(name=name, family="paragraph")
    tp = TextProperties(fontweight=fw, fontsize=fs)
    if name == "Code":
        tp = TextProperties(fontweight=fw, fontsize=fs, fontfamily="monospace")
    if name == "Caption":
        tp = TextProperties(fontweight=fw, fontsize=fs, fontstyle="italic")
    st.addElement(tp)
    pp = ParagraphProperties()
    if name in ("Title", "H1"):
        pp = ParagraphProperties(margintop="18pt", marginbottom="8pt")
    elif name in ("H2", "H3"):
        pp = ParagraphProperties(margintop="12pt", marginbottom="6pt")
    elif name == "Body":
        pp = ParagraphProperties(marginbottom="4pt")
    elif name == "Code":
        pp = ParagraphProperties(marginbottom="2pt", marginleft="10pt")
    elif name == "Caption":
        pp = ParagraphProperties(marginbottom="10pt")
    st.addElement(pp)
    styles.addElement(st)


def heading(text, level=1):
    tag = f"H{min(level, 3)}"
    doc.text.addElement(H(text=text, outlinelevel=str(level), stylename=tag))

def para(text, style="Body"):
    doc.text.addElement(P(text=text, stylename=style))

def code(text):
    for line in text.strip().split("\n"):
        doc.text.addElement(P(text=line, stylename="Code"))

def add_img(filename, width_cm=14):
    path = os.path.join(IMG_DIR, filename)
    if not os.path.exists(path):
        para(f"[IMAGE NOT FOUND: {filename}]")
        return
    p = P(stylename="Caption")
    href = doc.addPicture(path, width=f"{width_cm}cm")
    p.addElement(Image(href=href, width=f"{width_cm}cm"))
    doc.text.addElement(p)

def cap(text):
    doc.text.addElement(P(text=text, stylename="Caption"))

# ════════════════════════════════════════════════════════════════════════════
# CONTENT
# ════════════════════════════════════════════════════════════════════════════

# Title
doc.text.addElement(H(text="Anaviz — Project Explained", outlinelevel="0", stylename="Title"))
para("Dataset-Independent Time-Series Visualization & Evidence Layer")
para("")

# ── 1. What is Anaviz ──────────────────────────────────────────────────────
heading("1. What is Anaviz?", 1)
para("Anaviz is a dataset-independent time-series visualization tool. You bring your own HTTP data source + a config.json schema, and Anaviz adapts to it.")
para("Key principle: The project ships with ZERO preloaded datasets. Users register their own API endpoint through the UI or POST /api/configs.")
para("It works with ANY HTTP time-series API — CERN HLT data, InfluxDB, Prometheus, custom sensor APIs, etc.")
para("")

# ── 2. Architecture ────────────────────────────────────────────────────────
heading("2. Architecture — Two Independent Containers", 1)
para("Anaviz runs as TWO fully independent Docker containers that share ZERO code:")
para("• Datasource container (:9000) — owns the real HLT data, serves /archive/* API, TimescaleDB on :5434")
para("• Project container (:8000) — generic viz app, hydrates its own cache over HTTP, Postgres on :5433")
para("")
para("The project has NO knowledge of HLT. It works because a config.json tells it how to talk to the data source.")
para("")
add_img("06_architecture.png", 15)
cap("Figure 1: Two-container architecture")
para("")

code("""# docker-compose.yml (simplified):
services:
  datasource:
    build: ./datasource          # anaviz-datasource image
    ports: ["9000:9000", "5434:5432"]
    command: uvicorn anaviz_datasource.archive_server:app --port 9000

  project:
    build: . (docker/Dockerfile.project)  # anaviz-project image
    ports: ["8000:8000", "5433:5432"]
    depends_on: datasource        # waits for health check
    command: uvicorn server.app:app --port 8000""")

# ── 3. The Dataset ──────────────────────────────────────────────────────────
heading("3. The Dataset — ATLAS HLT p-beat", 1)
para("This is REAL hardware monitoring data from CERN's ATLAS experiment at the Large Hadron Collider (LHC).")
para("")
para("What is 'HLT p-beat'?")
para("• HLT = High Level Trigger — the real-time event selection system (decides which 40M collisions/sec to keep)")
para("• p-beat = Periodic heartbeat — monitoring rates showing events/second per computing node")
para("• HLT p-beat = heartbeat monitoring data from the HLT computing nodes")
para("")
para("The data consists of 3 large CSV files from Zenodo (record 7908064):")
para("• hlt_train_set.csv (~2 GB) — training data")
para("• hlt_test_set.csv (~1 GB) — test data")
para("• hlt_val_set.csv (~632 MB) — validation data")
para("")
para("CSV structure:")
para("• Rows = timestamps (~1 second intervals, April–October 2018, ~6 months)")
para("• Columns = 3,690 DCM computing nodes (hardware sensors)")
para("• Values = float Hz rates (events per second, range 0–0.60 Hz)")
para("• NaN = missing data (sensor offline)")
para("")
para("Channel naming example:")
para("  DF_IS:HLT-24:tpu-rack-16.DCM-NoTS:HLT-24:tpu-rack-16:pc-tdq-tpu-16002.info")
para("  → HLT_DCM_sub24_rack16_node16002")
para("  (subsystem 24, rack 16, node 16002)")
para("")
para("Element IDs start at 900000: Column 0 → element_id 900000, Column 1 → 900001, etc.")
para("")
add_img("01_raw_series.png", 14)
cap("Figure 2: Raw time-series from one HLT DCM channel — note the source gap at 5000–5100s")
para("")

code("""# From datasource/anaviz_datasource/ingest_hlt.py:
ELEMENT_ID_OFFSET = 900_000

def short_name(full_col, idx):
    # DF_IS:HLT-24:tpu-rack-16...pc-tdq-tpu-16002.info
    # → HLT_DCM_sub24_rack16_node16002
    sub = rack = "?"; node = str(idx)
    for part in full_col.split(":"):
        if part.startswith("HLT-"): sub = part.split("-")[-1]
        if "rack-" in part: rack = part.split("rack-")[-1].split(".")[0]
    if "tpu-" in full_col: node = full_col.split("tpu-")[-1].replace(".info", "")
    return f"HLT_DCM_sub{sub}_rack{rack}_node{node}" """)

# ── 4. Data Pipeline ────────────────────────────────────────────────────────
heading("4. Data Pipeline — Download → Ingest → Serve", 1)
para("Step 1: Download from Zenodo")
para("  anaviz-download — parallel 64 MiB segments via curl, MD5 verification, resumable")
para("")
code("""# From download_hlt.py:
ZENODO_RECORD = "https://zenodo.org/records/7908064/files"
# Downloads in 64 MiB parallel segments using HTTP Range requests
# Re-run resumes unfinished segments and verifies MD5 of finished files""")
para("")
para("Step 2: Ingest into TimescaleDB (datasource DB on :5434)")
para("  anaviz-ingest — reads CSV in 5000-row chunks, idempotent ON CONFLICT")
para("")
code("""# From ingest_hlt.py:
def ingest_file(path, col_to_eid, conn):
    # COPY to temp staging table → INSERT INTO eventhistory ON CONFLICT DO UPDATE
    cur.execute(
        "INSERT INTO eventhistory (element_id, ts, value, quality_flag) "
        "SELECT ... FROM _hlt_ingest_stage "
        "ON CONFLICT (element_id, ts) DO UPDATE SET value = EXCLUDED.value")
    # NaN values are skipped (not stored)""")
para("")
para("Step 3: Datasource serves raw data via /archive/* API on port 9000")
para("")
code("""# From archive_server.py:
GET /archive/channels       → list of (element_id, name)
GET /archive/extent         → {t_min, t_max}
GET /archive/eventhistory   → paginated rows: {t: [...], value: [...], quality_flag: [...]}
GET /archive/eventhistory.csv → streamed CSV download""")

# ── 5. The Config System ───────────────────────────────────────────────────
heading("5. The Config System — config.json (The Bridge)", 1)
para("The config.json tells the project HOW to talk to ANY HTTP time-series API.")
para("It's the bridge between the datasource and the visualization.")
para("")
para("Key sections of config.json:")
para("• source.base_url — where to fetch data (e.g. http://datasource:9000)")
para("• endpoints.series — path, shape (columnar/row), parameter mapping")
para("• mapping — how to extract timestamp, value, quality from the response")
para("• rollup_levels — opt-in bucket sizes in seconds [1, 4, 16, 64, 256]")
para("")
code("""{
  "source": {"base_url": "http://datasource:9000"},
  "endpoints": {
    "series": {
      "path": "/archive/eventhistory",
      "shape": "columnar",
      "parameters": {"entity": "element_id", "start": "t0", "end": "t1"}
    }
  },
  "mapping": {
    "timestamp": "t", "timestamp_format": "unix",
    "measures": {"value": {"value_path": "value"}},
    "quality": "quality_flag"
  },
  "rollup_levels": [1, 4, 16, 64, 256]
}""")
para("")
para("Fingerprinting: config changes create a new cache namespace via SHA-256 hash.")
para("Never corrupts another dataset's cached data.")
para("")
code("""# From server/api/config.py:
def fingerprint(self):
    canonical = json.dumps(json.loads(self.model_dump_json()), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]""")

# ── 6. Query Pipeline ──────────────────────────────────────────────────────
heading("6. Query Pipeline — From Click to Chart", 1)
para("When you click entities + date range + Go, here's what happens step by step:")
para("")
add_img("07_query_pipeline.png", 15)
cap("Figure 3: Query pipeline — 6 steps from frontend click to chart render")
para("")

para("Step 1: Frontend sends POST /api/query with entity_ids, range, resolution, algo")
para("Step 2: FastAPI routes to ConfigurableHttpAdapter (the right adapter for this dataset)")
para("Step 3: Hydrate — fetch uncovered ranges from /archive/* into cache_series table")
para("Step 4: Select tier — raw or rollup based on time span and pixel budget")
para("Step 5: Downsample — LTTB/M4/MINMAXLTTB to fit pixel budget (e.g. 1000px × 2 = 2000 pts)")
para("Step 6: Return CanonicalSeries with fidelity metadata, gaps, quality summary, metrics")
para("")

code("""# From server/api/configurable.py — _hydrate():
covered = await self.cache.coverage(entity_id, measure_id, start, end)
gaps = coverage_gaps(start, end, covered)   # uncovered time ranges
for a, b in gaps:
    rows, truncated = await self._fetch_series(raw, measure_id, a, b)
    await self.cache.store(entity_id, measure_id, rows, intervals)""")

# ── 7. Downsampling ────────────────────────────────────────────────────────
heading("7. Downsampling Algorithms", 1)
para("The pixel budget is: pixel_width × points_per_pixel (e.g. 1000 × 2 = 2000 max points)")
para("")
para("Three algorithms:")
para("• M4 — First/min/max/last per bucket. Best for most data. Default choice.")
para("• LTTB — Largest-Triangle-Three-Buckets. Best for smooth continuous signals.")
para("• MINMAXLTTB — LTTB + guaranteed min/max inclusion. When peaks/valleys matter.")
para("• RAW — No downsampling. Exact data.")
para("")
add_img("02_downsampling.png", 14)
cap("Figure 4: Downsampling comparison — RAW (8000 pts) vs M4 (~800 pts) vs LTTB (~200 pts)")
para("")

code("""# From server/downsample/__init__.py:
def downsample(payload, algo, budget):
    if algo == "M4":
        idx, ts, vals = m4_series(payload["t"], payload["avg"], budget, ...)
    elif algo == "MINMAXLTTB":
        idx, counts, starts = minmax_lttb_indices(payload["t"], payload["avg"], budget)
    else:  # LTTB
        idx = lttb_indices(payload["t"], payload["avg"], budget)
    result = {key: arr[idx] for key, arr in payload.items()}
    return result, elapsed_ms""")

# ── 8. Rollup Tiers ────────────────────────────────────────────────────────
heading("8. Rollup Tiers — Opt-in Aggregation", 1)
para("For large datasets, rollup tiers aggregate raw rows into time buckets BEFORE downsampling.")
para("This is strictly opt-in — nothing is derived automatically:")
para("• rollup_levels: [1, 4, 16, 64, 256] — explicit bucket sizes in seconds")
para("• rollup_enabled: false — master kill-switch (default true)")
para("• Without rollup_levels, every range is served from raw rows")
para("")
add_img("03_rollup_buckets.png", 14)
cap("Figure 5: Rollup bucket aggregation — 60s buckets with first/min/max/last/avg")
para("")
add_img("04_tier_selection.png", 14)
cap("Figure 6: Tier selection — small spans use RAW, large spans use coarser buckets")
para("")

code("""# From server/downsample/rollup.py:
def select_tier(span_s, pixel_width, points_per_pixel, levels):
    target = span_s / (pixel_width * points_per_pixel)
    # Pick coarsest bucket <= target (or 'raw' if too small)
    chosen = levels[0]
    for level in levels:
        if level <= target:
            chosen = level
    return chosen  # or "raw" if target < finest * 4""")
para("")
para("Each bucket stores: first_value, min_value, max_value, last_value, avg, count, worst_quality")
para("Tie-breaking: min keeps earliest timestamp, max keeps latest timestamp.")

# ── 9. Gap Preservation ────────────────────────────────────────────────────
heading("9. Gap Preservation — The Evidence Layer", 1)
para("Anaviz NEVER interpolates across data gaps. Missing data is evidence, not noise.")
para("")
para("How it works:")
para("1. find_gap_intervals() detects gaps in the time array (t[i+1] - t[i] > step * 2)")
para("2. Gaps returned in response as [[gap_start, gap_end], ...]")
para("3. Frontend inserts null values at gap midpoints → line breaks in chart")
para("4. Tooltip shows: observed vs interpolated, sample count, quality flag")
para("")
add_img("05_gap_preservation.png", 14)
cap("Figure 7: Gap preservation — line breaks at missing data (correct) vs interpolation (wrong)")
para("")

code("""# From frontend/utils.js — gap-aware interpolation:
function interpTo(allT, tArr, vArr, rawMode, gaps) {
    for (let i = 0; i < m; i++) {
        const inGap = x > gaps[gapIdx][0] && x < gaps[gapIdx][1];
        if (inGap) {
            vals[i] = null;  // ← gap preserved as null, never interpolated
        } else {
            vals[i] = vArr[j] + (vArr[j+1] - vArr[j]) * f;  // linear interp only between observed points
        }
    }
}""")

# ── 10. No-Data-Loss ───────────────────────────────────────────────────────
heading("10. No-Data-Loss — Dense Range Handling", 1)
para("When cached rows > row_cap, the system NEVER truncates.")
para("Instead, ALL rows are aggregated in Postgres into buckets sized to the pixel budget:")
para("• Each bucket: first/min/max/last/avg/count")
para("• Min/max envelope preserved exactly")
para("• Every source sample contributes to the result")
para("")
add_img("08_no_data_loss.png", 14)
cap("Figure 8: No-data-loss — truncation (wrong) vs SQL bucket aggregation over ALL rows (correct)")
para("")

code("""# From server/api/configurable.py — read_bucketed():
# Aggregates ALL rows in Postgres:
SELECT idx * bucket_s AS bucket_start,
       (array_agg(v ORDER BY t))[1] AS v_first,
       min(v) AS v_min, max(v) AS v_max,
       (array_agg(v ORDER BY t DESC))[1] AS v_last,
       avg(v) AS v_avg, count(*) AS n
FROM (SELECT floor(extract(epoch FROM ts) / bucket_s) AS idx, ...
      FROM cache_series WHERE ...) sub
GROUP BY idx ORDER BY idx""")

# ── 11. Generic Cache ──────────────────────────────────────────────────────
heading("11. The Generic Cache — 3 Shared Tables", 1)
para("The project cache has 3 tables shared across ALL datasets:")
para("• cache_series — raw time-series rows (isolated by dataset_key)")
para("• cache_coverage — tracks which ranges are cached (avoids re-probing)")
para("• cache_rollup — opt-in bucket aggregation tiers")
para("")
para("Isolation: dataset_key = 'dataset_id:config_fingerprint'")
para("A new config = new fingerprint = fresh namespace. Never corrupts other datasets.")
para("")
code("""# From db/cache_schema.sql:
CREATE TABLE cache_series (
    dataset_key TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    measure_id  TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    quality     SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, ts)
);""")

# ── 12. Frontend ───────────────────────────────────────────────────────────
heading("12. The Frontend — Vanilla JS + uPlot", 1)
para("No build step, no framework. Pure vanilla JavaScript.")
para("")
para("• chart.js — uPlot time-series with band envelopes, zoom/pan, hover tooltip")
para("• heatmap.js — Canvas heatmap overlay (temporal rolling z-score)")
para("• histogram.js — Per-entity value distribution histograms")
para("• app.js — Dataset discovery, state management, API calls, coordination")
para("• utils.js — Response adaptation, gap-aware interpolation, shared helpers")
para("")
para("Key features:")
para("• Zoom with mouse wheel (factor-based), pan with mouse drag")
para("• Hover sync between chart ↔ heatmap ↔ histograms")
para("• Evidence tooltip: observed/interpolated, sample count, quality flag")
para("• Auto-resize on panel toggle")
para("")
code("""// From frontend/chart.js:
// Band envelope (min/max) rendered as fill between two invisible series:
if (bandByEntity?.[i]) {
    series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
    series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
    bands.push({ series: [base + 2, base + 1], fill: color + '26' });
}""")

# ── 13. Tests ──────────────────────────────────────────────────────────────
heading("13. Test Suite — 72+ Offline Tests", 1)
para("All tests run WITHOUT a database. No Docker needed.")
para("")
para("• test_config.py — Config validation, env substitution, fingerprints, loader")
para("• test_configurable.py — HTTP extraction, pagination, gap preservation, opaque IDs")
para("• test_rollup.py — Tier selection math, bucket aggregation, backfill, no-double-count")
para("• test_downsample.py — LTTB/M4/MINMAXLTTB band correctness")
para("• test_canonical_contract.py — Pydantic model compliance")
para("• test_data_fidelity.py — No-data-loss guarantees")
para("")
code("""pytest tests/ -q                                         # run all tests
python3 -m compileall server datasource/anaviz_datasource # verify syntax
for f in frontend/*.js; do node --check "$f"; done        # verify JS""")

# ── 14. Summary ────────────────────────────────────────────────────────────
heading("14. Summary", 1)
para("Anaviz is a dataset-independent time-series visualization tool:")
para("")
para("1. Works with ANY HTTP time-series API via config.json")
para("2. Preserves data fidelity — never interpolates across gaps")
para("3. Never truncates dense ranges — SQL bucket aggregation over ALL rows")
para("4. Two fully independent containers (datasource + project, zero shared code)")
para("5. Ships with ZERO preloaded datasets — users bring their own data")
para("6. 72+ offline unit tests — no database required")
para("7. Opt-in rollup tiers for large datasets")
para("8. Evidence layer: quality flags, sample counts, gap markers")
para("")
para("The HLT p-beat dataset is the first example, but the architecture works with any time-series data.")
para("")
para("Quick commands:")
code("""sudo docker compose up -d --build          # start both containers
curl http://localhost:9000/archive/extent   # check datasource
curl http://localhost:8000/api/datasets     # check project
pytest tests/ -q                           # run tests
open http://localhost:8000                  # open browser""")


# ── Save ────────────────────────────────────────────────────────────────────
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Anaviz_Presentation.odt")
doc.save(out)
print(f"✓ ODT saved: {out}")
