#!/usr/bin/env python3
"""Build a detailed, presentation-ready Anaviz ODT.

This is documentation generation only. It does not import or start Anaviz,
FastAPI, the datasource, PostgreSQL, or the frontend.
"""
from pathlib import Path

from odf.opendocument import OpenDocumentText
from odf.style import ParagraphProperties, Style, TextProperties
from odf.text import H, P
from odf.draw import Frame, Image

ROOT = Path(__file__).resolve().parent
IMG = ROOT / "images"
OUT = ROOT / "ANAVIZ_DETAILED_PRESENTATION.odt"

doc = OpenDocumentText()
styles = doc.styles


def add_style(name, size, weight="normal", family="Liberation Sans",
              italic=False, top="0pt", bottom="5pt", left="0pt"):
    style = Style(name=name, family="paragraph")
    props = TextProperties(
        fontsize=size,
        fontweight=weight,
        fontfamily=family,
        fontstyle="italic" if italic else "normal",
    )
    style.addElement(props)
    style.addElement(ParagraphProperties(
        margintop=top, marginbottom=bottom, marginleft=left))
    styles.addElement(style)


add_style("TitleLarge", "26pt", "bold", top="4pt", bottom="10pt")
add_style("Subtitle", "14pt", bottom="12pt")
add_style("H1Custom", "18pt", "bold", top="18pt", bottom="8pt")
add_style("H2Custom", "14pt", "bold", top="12pt", bottom="6pt")
add_style("H3Custom", "12pt", "bold", top="8pt", bottom="4pt")
add_style("BodyCustom", "10.5pt", bottom="5pt")
add_style("BulletCustom", "10.5pt", bottom="3pt", left="12pt")
add_style("CodeCustom", "8.5pt", family="Liberation Mono", bottom="2pt", left="10pt")
add_style("CaptionCustom", "9pt", italic=True, bottom="9pt")
add_style("CalloutCustom", "10.5pt", "bold", bottom="8pt", left="8pt")


def heading(text, level=1):
    style = {1: "H1Custom", 2: "H2Custom", 3: "H3Custom"}.get(level, "H3Custom")
    doc.text.addElement(H(text=text, outlinelevel=str(level), stylename=style))


def para(text, style="BodyCustom"):
    doc.text.addElement(P(text=text, stylename=style))


def bullet(text):
    para("• " + text, "BulletCustom")


def code(text):
    for line in text.strip("\n").split("\n"):
        doc.text.addElement(P(text=line, stylename="CodeCustom"))


def image(filename, width_cm=15):
    path = IMG / filename
    if not path.exists():
        para(f"[IMAGE NOT FOUND: {filename}]")
        return
    p = P(stylename="CaptionCustom")
    href = doc.addPicture(str(path))
    frame = Frame(width=f"{width_cm}cm", height="8.4cm", anchortype="as-char")
    frame.addElement(Image(href=href))
    p.addElement(frame)
    doc.text.addElement(p)


def figure_caption(text):
    para(text, "CaptionCustom")


# ---------------------------------------------------------------------------
# Title and speaking instructions
# ---------------------------------------------------------------------------
doc.text.addElement(H(text="Anaviz — Detailed Presentation Guide",
                      outlinelevel="0", stylename="TitleLarge"))
para("Dataset-independent time-series visualization, caching, downsampling, and data fidelity")
para("Source-backed explanations, code snippets, image narration, and likely questions", "Subtitle")
para("Use this document as both a presentation script and a technical reference. The code snippets are taken from the repository files listed in each section. The diagrams explain the behavior of the system; they are not all plots of the same physical data.", "CalloutCustom")

heading("How to present this project", 1)
para("Start with the problem, then explain the architecture, and only afterward explain the algorithms. A good short order is: what Anaviz is → two containers → config.json bridge → query pipeline → performance → data fidelity → frontend → conclusion.")
para("The main sentence to remember is: Anaviz connects to an arbitrary HTTP time-series API through config.json, caches only requested ranges, chooses an appropriate resolution, and returns a canonical response with evidence about gaps, quality, aggregation, and timing.")

heading("1. Elevator pitch", 1)
para("Say this in the first minute:")
para("Anaviz is a dataset-independent time-series visualization system. A user supplies an HTTP data source and a declarative config.json file. The configuration tells Anaviz where the source endpoints are and how to interpret their fields. The backend then fetches data on demand, stores it in a generic cache, selects raw or rollup data according to the screen resolution, downsamples it for the chart, and preserves important evidence such as missing intervals, quality flags, sample counts, and extrema.", "CalloutCustom")
para("The ATLAS HLT p-beat dataset is the first example, not a hard-coded limitation. The same frontend can visualize sensor readings, event rates, voltage, temperature, or another timestamp-and-value HTTP API if its response is described by config.json.")

heading("2. What problem does Anaviz solve?", 1)
bullet("Different APIs use different endpoint names, parameter names, timestamp formats, and response shapes.")
bullet("A long time range can contain far more rows than a browser can draw meaningfully.")
bullet("A missing sample is not the same as a zero and should not be hidden by a connecting line.")
bullet("A dense query must not stop at an arbitrary row limit and silently hide the rest of the time range.")
bullet("The frontend should not know database table names or source-specific identifiers.")
para("Anaviz addresses these problems with a declarative adapter, a canonical API contract, a range-aware cache, optional rollups, pixel-budget downsampling, and an evidence layer.")

heading("3. The example dataset: ATLAS HLT p-beat", 1)
para("The example data comes from CERN's ATLAS experiment at the Large Hadron Collider. HLT means High Level Trigger: the system that makes fast decisions about which collision events should be retained. A p-beat is a periodic heartbeat or monitoring rate from HLT computing nodes.")
bullet("The source contains approximately 3,690 DCM channels.")
bullet("The values represent rates, generally shown as events per second or Hz.")
bullet("The source is delivered as large train, test, and validation CSV files.")
bullet("CSV rows contain timestamps; columns represent hardware channels.")
bullet("NaN values mean that a channel has no valid sample at that timestamp.")
para("The datasource converts a CSV column into an entity ID and a readable label. Entity IDs are opaque to the frontend; the frontend receives them from the API and does not parse their internal meaning.")
code("""# datasource/anaviz_datasource/ingest_hlt.py
ELEMENT_ID_OFFSET = 900_000

# Column 0 -> element_id 900000
# Column 1 -> element_id 900001
# Column 2 -> element_id 900002""")
para("The ingestion code also creates readable names from the full DCM column name. This source-specific naming logic stays in the datasource package and is not embedded in the generic visualization renderer.")
code('''def short_name(full_col: str, idx: int) -> str:
    rack = "?"
    node = str(idx)
    sub = "?"
    for part in full_col.split(":"):
        if part.startswith("HLT-"):
            sub = part.split("-")[-1]
        if "rack-" in part:
            rack = part.split("rack-")[-1].split(".")[0]
    if "tpu-" in full_col:
        node = full_col.split("tpu-")[-1].replace(".info", "")
    return f"HLT_DCM_sub{sub}_rack{rack}_node{node}"''')
image("01_raw_series.png", 15)
figure_caption("Figure 1 — A raw source series. The highlighted interval has no source samples, so the evidence-aware chart must show a break rather than inventing a line.")

heading("4. Architecture: two independent containers", 1)
para("Anaviz deliberately separates the source of truth from the visualization project.")
bullet("Datasource container, port 9000: HLT-specific database and /archive/* HTTP API.")
bullet("Project container, port 8000: generic FastAPI application, cache, downsampling, and frontend.")
bullet("The project never imports the HLT datasource package and never writes to the datasource database.")
bullet("The only integration boundary is HTTP plus the user-supplied configuration.")
image("06_architecture.png", 15)
figure_caption("Figure 2 — The two-container architecture. The datasource owns the real data; the project fetches it over HTTP and maintains a separate generic cache.")
code("""# docker-compose.yml (simplified)
services:
  datasource:
    build: ./datasource
    ports: [\"9000:9000\", \"5434:5432\"]
    command: uvicorn anaviz_datasource.archive_server:app --port 9000

  project:
    build: .
    ports: [\"8000:8000\", \"5433:5432\"]
    depends_on: datasource
    command: uvicorn server.app:app --port 8000""")
para("Presentation line: The containers share a network connection and an HTTP contract, but they do not share code, database tables, or ownership of data.", "CalloutCustom")

heading("5. Datasource ingestion and archive API", 1)
heading("5.1 Download and ingest", 2)
para("The downloader is resumable because the CSV files are very large. The ingestion code reads the files in chunks rather than loading a multi-gigabyte file into memory. It skips NaN values, stages rows for COPY, and then merges them into the uniquely keyed eventhistory table.")
code("""# datasource/anaviz_datasource/ingest_hlt.py
chunksize = 5000
reader = pd.read_csv(
    path, index_col=0, parse_dates=True, chunksize=chunksize
)

for chunk_n, df in enumerate(reader):
    df.index = pd.to_datetime(df.index, utc=True)
    # convert valid cells into element_id, timestamp, value, quality rows
    # flush the buffer periodically instead of holding the whole file""")
code("""# Safe repeated ingestion
INSERT INTO eventhistory (element_id, ts, value, quality_flag)
SELECT element_id, ts, value, quality_flag
FROM _hlt_ingest_stage
ON CONFLICT (element_id, ts) DO UPDATE SET
  value = EXCLUDED.value,
  quality_flag = EXCLUDED.quality_flag""")
para("Explain idempotent ingestion as: running the same ingest again updates the same key instead of creating a duplicate. The temporary staging table is needed because PostgreSQL COPY itself does not provide an ON CONFLICT clause.")

heading("5.2 Archive HTTP endpoints", 2)
para("The archive server exposes source data without exposing the project to the source database schema. The important endpoints are:")
bullet("/archive/channels — entity IDs and readable names.")
bullet("/archive/extent — minimum and maximum timestamps.")
bullet("/archive/eventhistory — paginated raw rows for one entity and time range.")
bullet("/archive/eventhistory/count — number of source rows in a range.")
bullet("/archive/eventhistory.csv — streamed CSV representation.")
code("""# datasource/anaviz_datasource/archive_server.py
@app.get(\"/archive/eventhistory\")
async def eventhistory(
    element_id: int, t0: str, t1: str,
    offset: int = 0, limit: int = 50_000,
):
    # validate timestamps and entity existence
    # query only this entity and [t0, t1)
    # return columnar t, value, quality_flag arrays
    # return next_offset when another page exists""")

heading("6. config.json: the bridge to any HTTP API", 1)
para("config.json is declarative data. It contains endpoint paths, field mappings, timestamp rules, pagination, and optional performance settings. It contains no Python code, SQL, or templates.")
code("""{
  \"dataset\": {\"id\": \"hlt\", \"label\": \"ATLAS HLT Archive\"},
  \"source\": {\"type\": \"http\", \"base_url\": \"http://datasource:9000\"},
  \"endpoints\": {
    \"entities\": {\"path\": \"/archive/channels\"},
    \"extent\": {\"path\": \"/archive/extent\",
                 \"start_path\": \"t_min\", \"end_path\": \"t_max\"},
    \"series\": {
      \"path\": \"/archive/eventhistory\",
      \"shape\": \"columnar\",
      \"parameters\": {\"entity\": \"element_id\",
                     \"start\": \"t0\", \"end\": \"t1\"}
    }
  },
  \"mapping\": {
    \"entity_id\": \"element_id\",
    \"entity_label\": \"name\",
    \"timestamp\": \"t\",
    \"timestamp_format\": \"unix\",
    \"measures\": {\"value\": {\"value_path\": \"value\"}},
    \"quality\": \"quality_flag\"
  },
  \"rollup_levels\": [60, 300, 3600]
}""")
heading("6.1 What the configuration means", 2)
bullet("source.base_url tells the adapter where the HTTP API is located.")
bullet("endpoints.series.path tells it which endpoint returns a time series.")
bullet("shape=columnar says timestamps and values are parallel arrays.")
bullet("parameters maps canonical names to source-specific query parameters.")
bullet("mapping.timestamp and value_path identify the fields to extract.")
bullet("timestamp_format supports unix seconds, epoch milliseconds, or ISO text.")
bullet("rollup_levels explicitly opts the dataset into bucket tiers.")
code("""# server/api/config.py
class SeriesEndpoint(BaseModel):
    path: str
    shape: Literal[\"row\", \"columnar\"] = \"row\"
    parameters: dict[str, str] = {
        \"entity\": \"entity\", \"start\": \"start\", \"end\": \"end\"
    }

    @field_validator(\"parameters\")
    @classmethod
    def require_canonical_keys(cls, value):
        missing = {\"entity\", \"start\", \"end\"} - set(value)
        if missing:
            raise ValueError(\"series.parameters must map entity/start/end\")
        return value""")
para("This validation makes the adapter predictable. A configuration cannot omit the three pieces of information required to request a time range.")

heading("6.2 Configuration safety and cache fingerprint", 2)
code("""# Only DCSVIZ_* environment variables can be referenced
ENV_ALLOWLIST_PREFIX = \"DCSVIZ_\"

# A changed resolved config receives a new namespace
canonical = json.dumps(
    json.loads(self.model_dump_json()), sort_keys=True
)
fingerprint = hashlib.sha256(
    canonical.encode(\"utf-8\")
).hexdigest()[:16]""")
para("The fingerprint is important because changing a mapping or endpoint can change the meaning of cached data. A new configuration therefore receives a new cache namespace rather than accidentally reusing incompatible rows.")

heading("7. Canonical API contract", 1)
para("The adapter translates source-specific details into stable models in server/api/contracts.py. The frontend consumes the canonical contract and does not need to understand TimescaleDB, eventhistory, or HLT field names.")
code("""class SeriesQuery(BaseModel):
    dataset_id: str = \"default\"
    entity_ids: list[str]
    measure_ids: list[str]
    range: TimeRange
    resolution: ResolutionRequest
    downsampling: Literal[\"LTTB\", \"M4\", \"MINMAXLTTB\", \"RAW\"]
    filters: dict[str, list[str]] = {}""")
code("""class CanonicalSeries(BaseModel):
    entity_id: str
    points: SeriesPoints
    resolution: str | None
    gaps: list[list[float]]
    quality_summary: dict[str, int]
    fidelity: dict[str, Any]
    metrics: QueryMetrics""")
para("The response contains more than the line values. It records resolution, gaps, quality, fidelity, and query metrics. This lets the UI explain how a displayed value was produced rather than presenting an unexplained line.")

heading("8. Query pipeline: from click to chart", 1)
image("07_query_pipeline.png", 15)
figure_caption("Figure 3 — Six stages from a browser query to a canonical response.")
heading("8.1 Stage 1: the frontend sends a query", 2)
code("""// frontend/app.js
const response = await apiPost(\"/api/query\", {
  dataset_id: datasetId,
  entity_ids: selected.map(entity => String(entity.entity_id)),
  measure_ids: [measureId],
  range: { start: view.t0, end: view.t1 },
  resolution: {
    strategy: \"auto\",
    pixel_width: params.px,
    points_per_pixel: params.k,
  },
  downsampling: params.algo,
  filters: {},
}, signal);""")
heading("8.2 Stage 2: FastAPI routes to the adapter", 2)
code("""# server/app.py
@app.post(\"/api/query\")
async def canonical_query(request: SeriesQuery):
    return await _get_dataset_adapter(
        request.dataset_id
    ).query(request)""")
heading("8.3 Stage 3: hydrate uncovered ranges", 2)
code("""# server/api/configurable.py
covered = await self.cache.coverage(
    entity_id, measure_id, start, end
)
gaps = coverage_gaps(start, end, covered)
for a, b in gaps:
    rows, truncated = await self._fetch_series(
        raw_entity, measure_id, a, b
    )
    await self.cache.store(entity_id, measure_id, rows, intervals)""")
para("Hydration means filling the project cache from the configured source. It is lazy: data is fetched because the user requested that range, not because the application preloaded every dataset.")
heading("8.4 Stage 4: choose raw or rollup tier", 2)
para("The adapter computes a pixel budget from pixel_width multiplied by points_per_pixel. If the dataset declared rollup levels and the range is large enough, the adapter chooses a tier. Otherwise it uses raw cache rows.")
heading("8.5 Stage 5: downsample", 2)
para("The selected raw or bucketed data is reduced to a display-sized payload using RAW, M4, LTTB, or MINMAXLTTB. The raw cache is not replaced by the display result.")
heading("8.6 Stage 6: return evidence", 2)
code("""CanonicalSeries(
    entity_id=\"hlt:900000\",
    points=SeriesPoints(t=[...], value=[...], min=[...], max=[...]),
    resolution=\"5m\",
    gaps=[[5000.0, 5100.0]],
    fidelity={
        \"aggregated\": True,
        \"interpolated\": False,
        \"missing_intervals_preserved\": True,
        \"truncated\": False,
    },
    metrics=QueryMetrics(
        rows_source=35900,
        rows_scanned=2244,
        rows_returned=1000,
    ),
)""")

heading("9. The generic cache", 1)
para("The project cache uses fixed generic tables shared by all registered datasets. It does not create HLT-specific tables in the project container.")
code("""-- db/cache_schema.sql
CREATE TABLE cache_series (
    dataset_key TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    measure_id TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    quality SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, ts)
);

CREATE TABLE cache_coverage (
    dataset_key TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    measure_id TEXT NOT NULL,
    t_min TIMESTAMPTZ NOT NULL,
    t_max TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE cache_rollup (
    dataset_key TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    measure_id TEXT NOT NULL,
    bucket_s DOUBLE PRECISION NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    n INTEGER NOT NULL,
    v_first DOUBLE PRECISION NOT NULL,
    v_min DOUBLE PRECISION NOT NULL,
    v_max DOUBLE PRECISION NOT NULL,
    v_last DOUBLE PRECISION NOT NULL,
    v_avg DOUBLE PRECISION NOT NULL
);""")
para("Coverage means the source range has been queried; it does not promise that every timestamp contains a value. This distinction is what allows internal source gaps to remain visible.")

heading("10. Rollup tiers", 1)
para("Rollups aggregate raw samples into explicitly configured time buckets. Every bucket stores first, minimum, maximum, last, average, count, timestamps of extrema, and the worst quality flag.")
image("03_rollup_buckets.png", 14)
figure_caption("Figure 4 — A 60-second bucket retains first, minimum, maximum, last, average, and the min/max envelope.")
code("""# server/downsample/rollup.py
def select_tier(span_s, pixel_width, points_per_pixel, levels=None):
    if span_s <= 0 or not levels:
        return \"raw\"
    levels = sorted(levels)
    target = span_s / max(pixel_width * points_per_pixel, 1)
    if target < levels[0] * 4:
        return \"raw\"
    chosen = levels[0]
    for level in levels:
        if level <= target:
            chosen = level
    return chosen""")
para("The target is the amount of time represented by one output point. A tier is useful only when it meaningfully reduces the row count, so very short ranges remain raw.")
image("04_tier_selection.png", 14)
figure_caption("Figure 5 — Tier selection changes with the time span and the fixed screen budget. The bucket values are illustrative configured levels.")

heading("11. Downsampling algorithms", 1)
para("The display budget is pixel_width × points_per_pixel. For example, 1,000 pixels and two points per pixel produce a target of approximately 2,000 output points.")
bullet("RAW: returns the selected data without downsampling.")
bullet("M4: keeps first, minimum, maximum, and last representatives in buckets.")
bullet("LTTB: selects points that preserve the visual shape using triangle areas.")
bullet("MINMAXLTTB: combines shape selection with min/max protection.")
image("02_downsampling.png", 14)
figure_caption("Figure 6 — RAW, M4, and LTTB show different point budgets and visual trade-offs. These are algorithm demonstrations, not a claim that all three plots use the same HLT scale.")
code("""# server/downsample/__init__.py
def downsample(payload, algo, budget):
    n = len(payload[\"t\"])
    if n == 0 or algo == \"RAW\":
        return payload, 0.0

    if algo == \"M4\":
        idx, ts, vals = m4_series(
            payload[\"t\"], payload[\"avg\"], budget, ...
        )
    elif algo == \"MINMAXLTTB\":
        idx, counts, starts = minmax_lttb_indices(
            payload[\"t\"], payload[\"avg\"], budget
        )
    else:
        idx = lttb_indices(payload[\"t\"], payload[\"avg\"], budget)

    return result, elapsed_ms""")
para("For bucketed payloads, the adapter also carries min and max arrays. The downsampling layer recomputes the displayed envelope so the chart can show uncertainty or range information around the representative value.")

heading("12. Gap preservation: the evidence layer", 1)
para("A missing sample is not converted into zero. The backend detects timestamp gaps and returns them. The frontend creates a null marker inside the declared interval so line renderers break visibly.")
code("""# server/api/common.py
def find_gap_intervals(timestamps, bucket_seconds):
    if len(timestamps) < 2:
        return []
    deltas = np.diff(np.asarray(timestamps, dtype=float))
    positive = deltas[deltas > 0]
    expected = (float(bucket_seconds)
                if bucket_seconds
                else float(np.median(positive)))
    threshold = expected * (2.5 if bucket_seconds else 1.5)
    return [[float(start), float(end)]
            for start, end, delta in zip(
                timestamps[:-1], timestamps[1:], deltas)
            if delta > threshold]""")
image("05_gap_preservation.png", 14)
figure_caption("Figure 7 — The correct chart leaves a break. The incorrect chart draws a misleading interpolated line across the missing interval.")
code("""// frontend/utils.js
export function buildSharedTimeAxis(results) {
  const timestamps = [];
  results.forEach(result => {
    timestamps.push(...(result.series?.t || []));
    (result.gap_intervals || []).forEach(([start, end]) => {
      timestamps.push(start + (end - start) / 2);
    });
  });
  return [...new Set(timestamps)].sort((a, b) => a - b);
}

// Inside a declared gap, the aligned value is null.
if (inGap) {
  vals[i] = null;
}""")
para("The frontend can align separate series onto one shared x-axis, but it does not fabricate values inside source gaps. Tooltips also distinguish observed values from derived display values.")

heading("13. Dense ranges and no-data-loss handling", 1)
para("row_cap is a memory and read-safety threshold, not permission to cut off the visualization. When cached rows exceed row_cap, the adapter uses SQL bucket aggregation over the full requested range.")
image("08_no_data_loss.png", 14)
figure_caption("Figure 8 — Truncating at row_cap would stop the chart early. The correct path aggregates all rows into pixel-sized buckets and preserves the full range and extrema.")
code("""# server/api/configurable.py
if total > self.config.row_cap:
    span_s = (end - start).total_seconds()
    bucket_s = span_s / max(budget, 1)
    data = await self.cache.read_bucketed(
        entity_id, measure_id, bucket_s, start, end,
        max(self.config.row_cap, budget)
    )""")
code("""# read_bucketed() aggregates every matching cache_series row
SELECT idx * bucket_s AS bucket_start,
       (array_agg(v ORDER BY t))[1] AS v_first,
       min(v) AS v_min,
       max(v) AS v_max,
       (array_agg(v ORDER BY t DESC))[1] AS v_last,
       avg(v) AS v_avg,
       count(*) AS n
FROM (...) sub
GROUP BY idx
ORDER BY idx""")
para("This is a summary representation, not a claim that every raw sample can be drawn as a separate browser point. The raw samples remain in cache_series, while the bucket carries exact first/min/max/last values, average, count, and quality information for the full range.", "CalloutCustom")

heading("14. Frontend: chart, heatmap, and histograms", 1)
para("The frontend is written in vanilla JavaScript. app.js manages state and API calls. chart.js manages the uPlot time-series chart. heatmap.js draws a Canvas matrix. histogram.js creates per-entity distributions. utils.js adapts canonical responses and handles time alignment.")
heading("14.1 Chart", 2)
bullet("uPlot draws multiple time series with zoom and pan.")
bullet("The min/max envelope can be rendered as a transparent band.")
bullet("The tooltip shows timestamp, value, observed/derived status, sample count, and quality.")
bullet("The chart cursor can synchronize with the heatmap.")
code("""// frontend/chart.js
if (bandByEntity?.[i]) {
  // invisible lower and upper series create a filled min/max band
  series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
  series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
  bands.push({ series: [base + 2, base + 1], fill: color + '26' });
}""")
heading("14.2 Heatmap", 2)
para("The matrix endpoint bins values into time columns and computes a temporal rolling z-score. A z-score describes how unusual the current value is compared with the recent baseline. Empty bins remain null. The Canvas heatmap maps low, normal, and high scores to different colors and synchronizes its time position with the live chart x-scale.")
code("""# server/api/common.py
def rolling_zscore(values, window):
    # compare each observed value with the trailing observed baseline
    # skip non-finite values instead of treating them as zero
    ...

# matrix response preserves missing bins
missing_value = None""")
heading("14.3 Histograms", 2)
para("Histograms are built from the last chart data, so they show per-entity value distributions without starting another source query. Hover synchronization lets the user connect distribution information with the time-series view.")

heading("15. Complete data flow", 1)
para("Use this compact explanation while pointing at the architecture and query images:")
code("""1. Download CSV files from Zenodo.
2. Ingest valid samples into the datasource TimescaleDB.
3. Serve raw rows from /archive/* over HTTP.
4. Register the HTTP API with config.json.
5. Frontend sends POST /api/query.
6. Adapter finds uncovered ranges.
7. Adapter fetches and stores missing ranges in cache_series.
8. Adapter records coverage in cache_coverage.
9. Optional rollups are built in cache_rollup.
10. Raw or rollup tier is selected from the pixel budget.
11. Dense raw ranges use full SQL bucket aggregation.
12. M4, LTTB, or MINMAXLTTB produces display-sized output.
13. CanonicalSeries returns values plus gaps, quality, fidelity, and metrics.
14. Browser renders chart, heatmap, and histograms.""")

heading("16. Important design guarantees", 1)
bullet("Dataset independence: the project uses a generic adapter and canonical contract.")
bullet("Source isolation: the datasource database remains separate from the project cache.")
bullet("Lazy loading: only requested ranges are hydrated.")
bullet("Cache isolation: dataset ID plus configuration fingerprint separates namespaces.")
bullet("Missingness preservation: absent samples remain absent or null.")
bullet("Gap preservation: the chart does not connect across declared source gaps.")
bullet("No silent truncation: dense ranges are aggregated over all cached rows.")
bullet("Quality preservation: quality flags and worst bucket quality are returned.")
bullet("Provenance: the response identifies tier, aggregation, interpolation, and timing information.")

heading("17. What is inside demo/?", 1)
bullet("ANAVIZ_PROJECT_EXPLAINED.md — the long source-backed explanation.")
bullet("PRESENTATION_CHEAT_SHEET.md — short speaking notes and anticipated questions.")
bullet("generate_odt.py — an earlier ODT document generator.")
bullet("generate_detailed_odt.py — this dedicated detailed-document generator.")
bullet("ANAVIZ_DETAILED_PRESENTATION.odt — the generated presentation document.")
bullet("images/ — the eight architecture and algorithm diagrams.")
para("The ODT generator is documentation tooling only. It creates styles, writes headings and code paragraphs, embeds the PNGs, and saves an ODT. It is not started by Docker and is not part of the runtime query path.")

heading("18. Presentation-ready conclusion", 1)
para("Anaviz is more than a chart. It is a generic boundary between an HTTP data source and a visualization frontend. config.json removes source-specific integration code. The cache avoids repeated requests. Rollups and downsampling make large ranges usable. Gap markers, quality flags, sample counts, and fidelity metadata prevent the chart from hiding uncertainty. The two-container architecture protects the real datasource from project cache operations.", "CalloutCustom")
para("Final sentence: The HLT dataset demonstrates the system, but the architecture is designed for any timestamped HTTP data source.")

heading("19. Questions you may be asked", 1)
heading("Why not use Grafana?", 2)
para("Grafana usually needs a datasource plugin or a compatible query implementation. Anaviz uses a declarative configuration, so a new HTTP source can be connected without writing a new renderer integration.")
heading("Does downsampling destroy the original data?", 2)
para("No. Raw rows remain in the project cache. Downsampling changes only the response sent to the chart. Bucket responses preserve extrema, first/last values, counts, and quality information.")
heading("What happens when the cache is cleared?", 2)
para("Only the project cache is cleared. The datasource database is separate and remains intact.")
heading("Why is missing data not converted to zero?", 2)
para("Zero is a valid measurement. Missing means that no measurement was observed. Converting missing to zero would create false evidence.")
heading("Are rollups automatic?", 2)
para("No. Rollups are opt-in through explicit rollup_levels. Without levels, the adapter serves ranges from raw cache rows, with SQL bucketing available for dense no-truncation handling.")
heading("Can this become real-time?", 2)
para("The current system is pull-based and query-on-demand. A future WebSocket layer could add live updates while retaining the same adapter and canonical response design.")
heading("What does the heatmap show?", 2)
para("It shows a temporal rolling z-score for each entity and time bin. It highlights values that are unusually low or high compared with the recent observed baseline and leaves missing bins unscored.")
heading("How does it scale?", 2)
para("It uses pagination, on-demand caching, optional rollup tiers, SQL aggregation over dense ranges, and a pixel-based output budget. The browser receives a display-sized response rather than every raw sample.")

heading("20. Final cheat sheet", 1)
bullet("Anaviz = generic time-series explorer, not an HLT-only viewer.")
bullet("Datasource = source of truth on port 9000.")
bullet("Project = generic API, cache, algorithms, and frontend on port 8000.")
bullet("config.json = bridge between arbitrary HTTP API and canonical model.")
bullet("cache_series = raw requested samples.")
bullet("cache_coverage = ranges already checked.")
bullet("cache_rollup = optional first/min/max/last/average bucket summaries.")
bullet("M4/LTTB/MINMAXLTTB = display reduction algorithms.")
bullet("Gaps = visible missing intervals, never silently filled.")
bullet("row_cap = trigger for full SQL bucket aggregation, not permission to truncate.")
bullet("Frontend = chart + heatmap + histograms + evidence tooltips.")
bullet("The strongest claim = fast visualization without hiding data quality.")

# Save the documentation artifact.
doc.save(str(OUT))
print(f"Saved {OUT}")
