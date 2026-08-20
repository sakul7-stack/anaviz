#!/usr/bin/env python3
"""Build the expanded, formatted Anaviz presentation ODT."""
from pathlib import Path

from odf.opendocument import OpenDocumentText
from odf.style import ParagraphProperties, Style, TextProperties
from odf.text import H, P
from odf.draw import Frame, Image

ROOT = Path(__file__).resolve().parent
IMG = ROOT / "images"
OUT = ROOT / "ANAVIZ_FULL_DETAILED_PRESENTATION.odt"

doc = OpenDocumentText()
styles = doc.styles


def style(name, size, color="#202124", weight="normal", family="Liberation Sans",
          italic=False, top="0pt", bottom="5pt", left="0pt"):
    s = Style(name=name, family="paragraph")
    s.addElement(TextProperties(
        fontsize=size, fontweight=weight, color=color,
        fontfamily=family, fontstyle="italic" if italic else "normal"))
    s.addElement(ParagraphProperties(
        margintop=top, marginbottom=bottom, marginleft=left))
    styles.addElement(s)


style("TitleBig", "28pt", "#124e8c", "bold", top="10pt", bottom="8pt")
style("Subtitle", "15pt", "#4b5563", bottom="14pt")
style("H1Blue", "19pt", "#124e8c", "bold", top="18pt", bottom="8pt")
style("H2Blue", "14pt", "#1f6f8b", "bold", top="12pt", bottom="5pt")
style("H3Blue", "11.5pt", "#6f42c1", "bold", top="8pt", bottom="4pt")
style("Body", "10.5pt", bottom="5pt")
style("Bullet", "10.5pt", bottom="3pt", left="12pt")
style("Code", "8.5pt", "#17324d", "normal", "Liberation Mono", bottom="2pt", left="10pt")
style("CodeLabel", "9pt", "#6f42c1", "bold", bottom="2pt", left="10pt")
style("Explain", "10pt", "#185c37", "normal", bottom="7pt", left="10pt")
style("Say", "10.5pt", "#8a4b08", "bold", bottom="8pt", left="8pt")
style("Caption", "9pt", "#5c6773", "normal", "Liberation Sans", True, bottom="9pt")
style("Small", "8.5pt", "#5c6773", bottom="3pt")


def h(text, level=1):
    sty = {1: "H1Blue", 2: "H2Blue", 3: "H3Blue"}.get(level, "H3Blue")
    doc.text.addElement(H(text=text, outlinelevel=str(level), stylename=sty))


def p(text, sty="Body"):
    doc.text.addElement(P(text=text, stylename=sty))


def bullet(text):
    p("• " + text, "Bullet")


def say(text):
    p("SAY THIS: " + text, "Say")


def snippet(source, text, explanation):
    p("CODE FROM " + source, "CodeLabel")
    for line in text.strip("\n").split("\n"):
        doc.text.addElement(P(text=line, stylename="Code"))
    p("PLAIN ENGLISH: " + explanation, "Explain")


def figure(filename, caption, width=15):
    path = IMG / filename
    if not path.exists():
        p("[Missing image: " + filename + "]", "Caption")
        return
    para = P(stylename="Caption")
    href = doc.addPicture(str(path))
    frame = Frame(width=f"{width}cm", height="8.4cm", anchortype="as-char")
    frame.addElement(Image(href=href))
    para.addElement(frame)
    doc.text.addElement(para)
    p(caption, "Caption")


# Cover page
doc.text.addElement(H(text="ANAVIZ", outlinelevel="0", stylename="TitleBig"))
p("FULL DETAILED PRESENTATION GUIDE", "Subtitle")
p("Dataset-independent time-series visualization and evidence-aware data handling", "Subtitle")
p("Prepared from the repository implementation and demo diagrams", "Small")
p("", "Body")
p("The purpose of this document is to help explain the code, not merely show the final UI. Every important code snippet is followed by a simple explanation and a sentence you can say during the presentation.", "Say")
p("Presentation order: problem → dataset → architecture → configuration → API contract → query pipeline → cache → rollups → downsampling → gaps → no-data-loss → frontend → conclusion.")
h("The one-sentence answer", 1)
p("Anaviz lets any HTTP time-series API become a fast, generic visualization source through config.json, while preserving evidence about missing data, quality, aggregation, and provenance.", "Say")
h("Quick vocabulary", 1)
bullet("Datasource: the source-of-truth service that owns the real data.")
bullet("Project: the generic visualization service and its separate cache.")
bullet("Entity: a channel, sensor, node, or other time-series subject.")
bullet("Measure: the value being plotted, such as value or temperature.")
bullet("Rollup: a time bucket containing summary information.")
bullet("Downsampling: reducing display points to fit the screen.")
bullet("Fidelity: metadata explaining how the displayed result was produced.")

# Overview
h("1. What is Anaviz?", 1)
p("Anaviz is a dataset-independent time-series visualization system. It does not require the frontend to know the database schema of every possible source. Instead, the user registers an HTTP API and supplies a declarative config.json file.")
bullet("The application starts with zero preloaded datasets.")
bullet("A user can register an API in the Data sources panel or through POST /api/configs.")
bullet("The adapter translates source-specific JSON into a canonical Anaviz response.")
bullet("The frontend renders a line chart, optional heatmap, histograms, and evidence tooltips.")
say("This is not an HLT-only viewer. HLT is the first example. The reusable part is the adapter, cache, canonical contract, and visualization frontend.")
h("What problem is solved?", 2)
bullet("APIs disagree about paths, parameter names, timestamp formats, and JSON shape.")
bullet("Large ranges contain more data than a browser can draw clearly.")
bullet("Connecting lines across missing samples creates false evidence.")
bullet("A row cap must not silently stop a chart before the requested end time.")
bullet("A generic frontend should not parse source-specific IDs or table names.")

# Dataset and ingestion
h("2. Example dataset: ATLAS HLT p-beat", 1)
p("The example is real ATLAS monitoring data from CERN. HLT means High Level Trigger. P-beat is a periodic heartbeat or rate signal from HLT computing nodes, represented as timestamped measurements.")
bullet("Approximately 3,690 DCM channels are represented as entities.")
bullet("The data covers a long period and is distributed in very large CSV files.")
bullet("Each CSV column is a hardware channel; each row index is a timestamp.")
bullet("NaN means the channel has no valid observed sample at that timestamp.")
snippet("datasource/anaviz_datasource/ingest_hlt.py", """ELEMENT_ID_OFFSET = 900_000

# CSV columns become stable source entity IDs
# column 0 -> 900000
# column 1 -> 900001
# column 2 -> 900002""", "The importer gives every channel a numeric ID. The generic frontend later treats this ID as opaque and does not try to decode it.")
snippet("datasource/anaviz_datasource/ingest_hlt.py", """chunksize = 5000
reader = pd.read_csv(
    path, index_col=0, parse_dates=True, chunksize=chunksize
)

for chunk_n, df in enumerate(reader):
    df.index = pd.to_datetime(df.index, utc=True)""", "The file is read in 5,000-row pieces. This prevents a multi-gigabyte CSV from being loaded into memory all at once.")
figure("01_raw_series.png", "Figure 1 — A raw source-style series. The highlighted interval is a missing-data gap.")
say("The first image is useful because it introduces the main data-fidelity question: what should the chart do when the source has no samples?")

h("3. Two-container architecture", 1)
p("The datasource and project are separate applications with separate databases and separate responsibilities.")
bullet("Datasource container, port 9000: HLT database plus /archive/* API.")
bullet("Project container, port 8000: FastAPI routes, generic cache, rollups, downsampling, and frontend.")
bullet("The project talks to the datasource using HTTP, not direct database access.")
bullet("Clearing the project cache cannot destroy the source-of-truth data.")
figure("06_architecture.png", "Figure 2 — Source ingestion, datasource API, config bridge, project cache, and browser.")
snippet("docker-compose.yml (simplified)", """services:
  datasource:
    ports: [\"9000:9000\", \"5434:5432\"]

  project:
    ports: [\"8000:8000\", \"5433:5432\"]
    depends_on: datasource""", "The two services have different application ports and different database ports. The dependency only controls startup order; the project still communicates through the API boundary.")
say("Use the phrase source of truth for the datasource and generic visualization cache for the project.")

# Extra architecture/API image
h("4. New diagram: API contract translation", 1)
figure("09_api_contract_translation.png", "Figure 3 — The source API can use element_id and eventhistory, while the frontend receives generic entities, measures, points, gaps, and fidelity.")
p("The adapter is the translator. The source may return columnar arrays named t, value, and quality_flag. The canonical response uses points.t, points.value, points.quality, plus gaps and metrics.")
snippet("server/api/contracts.py", """class SeriesQuery(BaseModel):
    dataset_id: str = \"default\"
    entity_ids: list[str]
    measure_ids: list[str]
    range: TimeRange
    resolution: ResolutionRequest
    downsampling: Literal[\"LTTB\", \"M4\", \"MINMAXLTTB\", \"RAW\"]""", "This is the stable request format. A renderer can use it without knowing whether the source is HLT, Prometheus, or a custom sensor API.")
snippet("server/api/contracts.py", """class CanonicalSeries(BaseModel):
    entity_id: str
    points: SeriesPoints
    resolution: str | None
    gaps: list[list[float]]
    quality_summary: dict[str, int]
    fidelity: dict[str, Any]
    metrics: QueryMetrics""", "The response carries values plus an explanation of where they came from and what happened to the data on the way to the screen.")

# Config
h("5. config.json: the bridge", 1)
p("config.json describes an API without executing any code from the file. It says where the source is, how to request a range, and how to extract timestamps, values, and quality flags.")
snippet("user config.json", """{
  \"source\": {\"base_url\": \"http://datasource:9000\"},
  \"endpoints\": {
    \"series\": {
      \"path\": \"/archive/eventhistory\",
      \"shape\": \"columnar\",
      \"parameters\": {
        \"entity\": \"element_id\",
        \"start\": \"t0\", \"end\": \"t1\"
      }
    }
  },
  \"mapping\": {
    \"timestamp\": \"t\",
    \"measures\": {\"value\": {\"value_path\": \"value\"}}
  }
}""", "The left side uses Anaviz's canonical concepts. The values on the right side match the actual source API. That is why one generic adapter can support many APIs.")
p("The important configuration keys are:")
bullet("source.base_url: the API address.")
bullet("endpoints.entities: how to list entities.")
bullet("endpoints.extent: how to find the overall time range.")
bullet("endpoints.series: how to request values and whether the response is row or columnar.")
bullet("mapping: where ID, label, timestamp, value, and quality fields live.")
bullet("pagination: page size and maximum number of pages.")
bullet("rollup_levels: explicit optional time-bucket sizes.")
bullet("row_cap: threshold for dense-range bucket aggregation.")
snippet("server/api/config.py", """class HttpSource(BaseModel):
    type: Literal[\"http\"] = \"http\"
    base_url: str

    @field_validator(\"base_url\")
    def safe_url(cls, value):
        if not value.startswith((\"http://\", \"https://\")):
            raise ValueError(\"base_url must be an http(s) URL\")
        if \"@\" in value.split(\"://\", 1)[1]:
            raise ValueError(\"embedded credentials are not allowed\")
        return value.rstrip(\"/\")""", "Before the source is registered, the configuration is validated. It must use HTTP or HTTPS and cannot put a username or password directly in the URL.")

# Ingestion/API extraction
h("6. How source responses become rows", 1)
snippet("server/api/configurable.py", """# Columnar response: parallel arrays
for i in range(min(len(t_arr), len(v_arr))):
    ts = parse_source_timestamp(t_arr[i], fmt)
    value = coerce_number(v_arr[i])
    quality = _quality_flag(q_arr[i], quality_map)
    rows.append((ts, value, quality))""", "The adapter takes the timestamp, value, and quality at the same array index and converts them into one canonical internal row.")
snippet("server/api/configurable.py", """if raw_v is None or raw_v == missing:
    value = None
else:
    value = coerce_number(raw_v)

if value is None:
    continue  # do not turn missing into zero""", "Missing values are not stored as zeros. A missing sample means the source did not provide a usable observation.")

# Query
h("7. Query pipeline", 1)
figure("07_query_pipeline.png", "Figure 4 — Six stages from frontend request to canonical response.")
p("The user sees one chart request, but the backend performs several controlled steps.")
for line in [
    "1. Frontend sends POST /api/query.",
    "2. FastAPI selects the adapter for dataset_id.",
    "3. Adapter finds uncovered ranges.",
    "4. Adapter fetches those ranges and stores them in cache_series.",
    "5. Adapter chooses raw or rollup data.",
    "6. Adapter downsamples to the pixel budget.",
    "7. Adapter returns points plus gaps, fidelity, and metrics.",
]:
    bullet(line)
snippet("frontend/app.js", """const response = await apiPost(\"/api/query\", {
  dataset_id: datasetId,
  entity_ids: selected.map(e => String(e.entity_id)),
  measure_ids: [measureId],
  range: { start: view.t0, end: view.t1 },
  resolution: { pixel_width: params.px, points_per_pixel: 2 },
  downsampling: params.algo,
});""", "When the user chooses entities and a time range, the browser sends only the request description. The backend makes the storage and performance decisions.")
snippet("server/app.py", """@app.post(\"/api/query\")
async def canonical_query(request: SeriesQuery):
    adapter = _get_dataset_adapter(request.dataset_id)
    return await adapter.query(request)""", "The route is intentionally small. It selects the correct dataset adapter and delegates the real work to it.")
snippet("server/api/configurable.py", """covered = await self.cache.coverage(entity_id, measure_id, start, end)
gaps = coverage_gaps(start, end, covered)
for a, b in gaps:
    rows, truncated = await self._fetch_series(raw, measure_id, a, b)
    await self.cache.store(entity_id, measure_id, rows, intervals)""", "The cache is checked first. Only missing portions of the requested interval are fetched from the source, which makes repeated queries faster.")

# Cache
h("8. New diagram: the three generic cache layers", 1)
figure("10_cache_layers.png", "Figure 5 — Raw rows, coverage intervals, and optional rollup buckets are shared tables isolated by dataset_key.")
p("The project cache is generic. All registered datasets use the same table design, but each dataset and configuration receives a separate namespace.")
snippet("db/cache_schema.sql", """CREATE TABLE cache_series (
    dataset_key TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    measure_id TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    quality SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, ts)
);""", "cache_series stores the raw valid samples that the project has already fetched. The primary key prevents duplicate samples for the same dataset, entity, measure, and timestamp.")
snippet("server/api/app registration", """dataset_key = f\"{config.dataset.id}:{config.fingerprint()}\"
cache = GenericDatasetCache(pool, dataset_key)
adapter = ConfigurableHttpAdapter(config, cache)""", "The dataset ID and configuration fingerprint are combined into the cache namespace. A changed mapping cannot accidentally read old rows with a different meaning.")

# Rollups/downsampling
h("9. Rollup tiers", 1)
p("A rollup is a time bucket that summarizes many raw samples. Rollups are not automatically invented from the source sampling rate. The user opts in with explicit levels such as 60, 300, and 3600 seconds.")
figure("03_rollup_buckets.png", "Figure 6 — A bucket retains first, minimum, maximum, last, average, count, and quality information.")
snippet("server/downsample/rollup.py", """target = span_s / max(pixel_width * points_per_pixel, 1)

if target < levels[0] * 4:
    return \"raw\"

chosen = levels[0]
for level in sorted(levels):
    if level <= target:
        chosen = level
return chosen""", "target is the time represented by one display point. If a query is too small to benefit from aggregation, raw data wins. For a large query, the largest useful configured tier is selected.")
figure("04_tier_selection.png", "Figure 7 — Short ranges use raw data; wider ranges use progressively coarser configured tiers.")

h("10. Downsampling algorithms", 1)
p("Rollup selection and downsampling are related but not identical. A rollup changes the time resolution before reading. Downsampling then reduces the selected payload to a number suitable for the browser.")
bullet("RAW: no reduction.")
bullet("M4: first/min/max/last representatives per bucket.")
bullet("LTTB: shape-aware Largest-Triangle-Three-Buckets selection.")
bullet("MINMAXLTTB: shape-aware selection with extrema protection.")
figure("02_downsampling.png", "Figure 8 — Different algorithms preserve different visual details while fitting a point budget.")
snippet("server/downsample/__init__.py", """def downsample(payload, algo, budget):
    if len(payload[\"t\"]) == 0 or algo == \"RAW\":
        return payload, 0.0
    if algo == \"M4\":
        return m4_series(payload, budget)
    if algo == \"MINMAXLTTB\":
        return minmax_lttb(payload, budget)
    return lttb(payload, budget)""", "The dispatcher chooses one algorithm from the user request. The raw cache is not destroyed; only the response sent to the renderer is reduced.")
say("Do not say downsampling means deleting the data. Say it means choosing representative display points while keeping the source and cache evidence available.")

# Fidelity
h("11. Gap preservation", 1)
p("The evidence layer exists because a chart can be visually persuasive but factually wrong. Anaviz keeps missing intervals visible.")
figure("05_gap_preservation.png", "Figure 9 — A visible line break is correct; a line connecting across the gap is misleading.")
snippet("server/api/common.py", """deltas = np.diff(timestamps)
expected = configured_step or median_positive_delta
threshold = expected * gap_multiplier

return [[t0, t1] for t0, t1, delta in zip(
    timestamps[:-1], timestamps[1:], deltas)
    if delta > threshold
]""", "The system compares neighboring timestamps with the expected sampling interval. A much larger difference becomes a declared gap interval.")
snippet("frontend/utils.js", """if (inGap) {
    vals[i] = null;       // line renderer breaks here
} else {
    vals[i] = interpolated_display_value;
}""", "The renderer may align valid series onto one shared time axis, but it inserts null inside a source gap. Null is what makes the line visibly break.")

# No data loss
h("12. Dense ranges: no silent truncation", 1)
figure("08_no_data_loss.png", "Figure 10 — The wrong path stops at row_cap; the correct path aggregates all rows into pixel-sized SQL buckets.")
p("row_cap protects reads from becoming unbounded, but it is not permission to return only the beginning of a chart. If the cached count exceeds row_cap, the adapter uses full-range SQL bucket aggregation.")
snippet("server/api/configurable.py", """if total > self.config.row_cap:
    bucket_s = span_seconds / max(pixel_budget, 1)
    data = await self.cache.read_bucketed(
        entity_id, measure_id, bucket_s, start, end,
        max(self.config.row_cap, pixel_budget)
    )""", "The bucket width is chosen from the time span and display budget. This creates roughly one bucket per display-sized time interval instead of cutting off the range.")
snippet("server/api/configurable.py", """SELECT min(v), max(v), avg(v), count(*)
FROM cache_series
WHERE dataset_key = ...
GROUP BY time_bucket
ORDER BY time_bucket""", "Every matching cached row contributes to a bucket. The response is summarized, but the full requested time range and important extrema are represented.")

# Frontend
h("13. New diagram: frontend panels", 1)
figure("11_frontend_panels.png", "Figure 11 — One canonical response feeds the chart, heatmap, and histogram views.")
p("The frontend is vanilla JavaScript. app.js coordinates state and requests. chart.js draws the time-series view. heatmap.js draws a Canvas matrix. histogram.js builds distributions from the last chart data.")
snippet("frontend/chart.js", """if (bandByEntity?.[i]) {
    // hidden lower/upper series become a filled envelope
    series.push(hidden_min_series);
    series.push(hidden_max_series);
    bands.push({ fill: translucent_entity_color });
}""", "The band shows the min/max range around a representative value. It helps the user see variation inside an aggregated bucket.")
snippet("frontend/heatmap.js", """const score = row?.[column];
ctx.fillStyle = score == null
    ? missing_color
    : score_to_color(score);
ctx.fillRect(x, y, width, height);""", "A missing matrix cell gets a missing color instead of being treated as zero. A valid score is converted into a color based on how unusual it is.")
snippet("frontend/histogram.js", """function updateHistograms(selected, lastRenderData, bins) {
    // build distributions from the already rendered series
    // no new source query is needed
}""", "The histogram reuses the chart response, so hovering or opening the distribution panel does not require another archive request.")

# Code-to-behavior
h("14. New diagram: code to behavior", 1)
figure("12_code_to_behavior.png", "Figure 12 — Explain each snippet using three questions: what enters, what happens, and what the user sees.")
p("For presentation, do not read every line. Use each snippet as evidence for a design decision:")
bullet("Input: what request or data structure enters the function?")
bullet("Behavior: what decision or transformation happens?")
bullet("Output: what does the user see or what guarantee is produced?")
code_example = """request -> validate -> hydrate -> choose tier -> downsample -> response

response -> chart + heatmap + histogram + evidence tooltip"""
snippet("presentation summary", code_example, "This is the whole system in one line. The backend is responsible for correctness and performance decisions; the frontend is responsible for coordinated visual evidence.")

# API endpoints and frontend behavior
h("15. Main API endpoints", 1)
p("The project exposes generic routes for new behavior and keeps legacy facades for compatibility.")
bullet("GET /api/datasets — discover registered datasets.")
bullet("GET /api/configs — list registered configuration metadata.")
bullet("POST /api/configs — validate and register a configuration.")
bullet("GET /api/datasets/{id}/schema — discover measures and capabilities.")
bullet("GET /api/datasets/{id}/entities — list/search entities.")
bullet("GET /api/datasets/{id}/extent — get dataset time extent.")
bullet("POST /api/query — request one or more canonical series.")
bullet("POST /api/matrix — request the heatmap matrix transform.")
bullet("GET /api/clear-cache — clear project cache only.")
snippet("server/api/configurable.py", """return SeriesQueryResponse(
    query=request,
    series=series,
    provenance=QueryProvenance(
        dataset_id=self.dataset_id,
        adapter=\"configurable\",
        downsampling=algo,
        generated_at=now,
    ),
)""", "The response says which dataset and adapter produced the answer and which downsampling algorithm was used. This is provenance, not just plotting data.")

# File map
h("16. File-by-file explanation", 1)
bullet("datasource/anaviz_datasource/download_hlt.py — resumable source-file download.")
bullet("datasource/anaviz_datasource/ingest_hlt.py — CSV-to-datasource database ingestion.")
bullet("datasource/anaviz_datasource/archive_server.py — raw HLT HTTP API.")
bullet("server/app.py — FastAPI composition, registry, and routes.")
bullet("server/api/config.py — validates config.json and creates fingerprints.")
bullet("server/api/contracts.py — canonical request and response models.")
bullet("server/api/configurable.py — HTTP extraction, hydration, cache reads, rollups, fidelity.")
bullet("server/api/common.py — timestamps, coverage math, gaps, matrix helpers.")
bullet("server/downsample/rollup.py — tier selection and bucket aggregation.")
bullet("server/downsample/__init__.py — M4/LTTB/MINMAXLTTB dispatch.")
bullet("frontend/app.js — application state and API orchestration.")
bullet("frontend/chart.js — uPlot chart, cursor, tooltip, zoom, and bands.")
bullet("frontend/heatmap.js — synchronized Canvas heatmap.")
bullet("frontend/histogram.js — value distributions from fetched chart data.")
bullet("db/cache_schema.sql — generic project cache tables.")
bullet("demo/generate_full_presentation_odt.py — documentation generator only, not runtime.")

# Q&A
h("17. Questions and short answers", 1)
h("Why two containers?", 2)
p("The datasource owns the real data and HLT-specific code. The project owns generic visualization and cache behavior. This separation protects the source database and makes the project reusable.")
h("Does downsampling lose data?", 2)
p("The browser receives representative points, but raw rows remain in the cache. M4 and bucket aggregation also preserve extrema, first/last values, counts, and quality information.")
h("Why not make missing values zero?", 2)
p("Zero is a measurement. Missing means no measurement. Turning missing into zero would create false evidence.")
h("Are rollups always enabled?", 2)
p("No. They are opt-in through explicit rollup_levels. The system never derives tiers automatically from the source sampling step.")
h("What if the config changes?", 2)
p("The configuration fingerprint changes, so the cache namespace changes. Old data cannot be silently interpreted using a new mapping.")
h("How is a dense range handled?", 2)
p("The system aggregates all cached rows into pixel-sized SQL buckets instead of returning only the first row_cap rows.")
h("What does the heatmap show?", 2)
p("It shows a temporal rolling z-score per entity and time bin, while leaving missing bins unscored.")
h("Can another API be used?", 2)
p("Yes. If it exposes entities, extent, and timestamped series through HTTP, config.json can describe its paths, parameters, response shape, and fields.")

# Final script
h("18. Five-minute speaking script", 1)
p("Opening — 30 seconds: Anaviz is a generic HTTP time-series visualization system. The HLT dataset demonstrates it, but HLT-specific logic is isolated in the datasource.")
p("Architecture — 45 seconds: The datasource is the source of truth. The project talks to it through HTTP and uses a separate cache, so project cache operations cannot destroy raw data.")
p("Configuration — 45 seconds: config.json maps source fields and parameters to the canonical concepts used by the frontend. It replaces custom integration code.")
p("Query — 60 seconds: A query checks coverage, fetches only uncovered ranges, stores raw samples, chooses raw or rollup tier, downsamples to the pixel budget, and returns evidence metadata.")
p("Performance — 45 seconds: Rollups reduce large spans. M4/LTTB/MINMAXLTTB fit the response to the screen. Dense ranges use SQL aggregation over all rows instead of truncating.")
p("Fidelity — 45 seconds: Gaps remain visible, missing is not zero, quality is propagated, and the tooltip shows whether a point is observed or derived.")
p("Close — 30 seconds: The result is a reusable adapter and evidence-aware frontend. The same project can visualize a different HTTP time-series API by changing config.json.")

h("19. Final takeaway", 1)
p("Anaviz separates source-specific integration from generic visualization. The source API can have its own names and response shape. config.json describes that shape. The adapter translates it. The cache makes repeated queries efficient. Rollups and downsampling make large ranges usable. Gap markers, quality flags, counts, extrema, and provenance keep the visualization honest.", "Say")
p("Remember: the goal is not merely to draw a line quickly. The goal is to draw a useful line without hiding how the data was obtained, aggregated, or missing.")

# Save.
doc.save(str(OUT))
print(f"Saved {OUT}")
