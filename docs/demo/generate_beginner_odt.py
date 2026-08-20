#!/usr/bin/env python3
"""Generate a beginner-friendly, line-by-line Anaviz code guide ODT."""
from pathlib import Path

from odf.opendocument import OpenDocumentText
from odf.style import ParagraphProperties, Style, TextProperties
from odf.text import H, P
from odf.draw import Frame, Image

ROOT = Path(__file__).resolve().parent
IMG = ROOT / "images"
OUT = ROOT / "ANAVIZ_BEGINNER_IO_GUIDE.odt"

doc = OpenDocumentText()
styles = doc.styles


def add_style(name, size, color="#202124", weight="normal", family="Liberation Sans",
              italic=False, top="0pt", bottom="5pt", left="0pt"):
    s = Style(name=name, family="paragraph")
    s.addElement(TextProperties(
        fontsize=size, fontweight=weight, color=color,
        fontfamily=family, fontstyle="italic" if italic else "normal"))
    s.addElement(ParagraphProperties(
        margintop=top, marginbottom=bottom, marginleft=left))
    styles.addElement(s)


add_style("TitleBeginner", "28pt", "#124e8c", "bold", top="8pt", bottom="10pt")
add_style("SubtitleBeginner", "15pt", "#4b5563", bottom="14pt")
add_style("H1Beginner", "19pt", "#124e8c", "bold", top="18pt", bottom="8pt")
add_style("H2Beginner", "14pt", "#1f6f8b", "bold", top="12pt", bottom="6pt")
add_style("H3Beginner", "11.5pt", "#6f42c1", "bold", top="8pt", bottom="4pt")
add_style("BodyBeginner", "10.5pt", bottom="5pt")
add_style("BulletBeginner", "10.5pt", bottom="3pt", left="12pt")
add_style("CodeLine", "9pt", "#17324d", "normal", "Liberation Mono", bottom="1pt", left="12pt")
add_style("CodeSource", "9pt", "#6f42c1", "bold", bottom="3pt", left="8pt")
add_style("LineExplain", "9.5pt", "#185c37", bottom="5pt", left="26pt")
add_style("FlowBeginner", "9.8pt", "#084c61", "bold", bottom="4pt", left="8pt")
add_style("WhyExplain", "10pt", "#8a4b08", "bold", bottom="7pt", left="8pt")
add_style("SayExplain", "10.5pt", "#7a3e00", "bold", bottom="8pt", left="8pt")
add_style("CaptionBeginner", "9pt", "#5c6773", italic=True, bottom="9pt")
add_style("SmallBeginner", "8.5pt", "#5c6773", bottom="3pt")


def heading(text, level=1):
    sty = {1: "H1Beginner", 2: "H2Beginner", 3: "H3Beginner"}.get(level, "H3Beginner")
    doc.text.addElement(H(text=text, outlinelevel=str(level), stylename=sty))


def para(text, sty="BodyBeginner"):
    doc.text.addElement(P(text=text, stylename=sty))


def bullet(text):
    para("• " + text, "BulletBeginner")


def code_line(text):
    doc.text.addElement(P(text=text, stylename="CodeLine"))


def image(filename, caption, width=15):
    path = IMG / filename
    if not path.exists():
        para("[Missing image: " + filename + "]", "CaptionBeginner")
        return
    p = P(stylename="CaptionBeginner")
    href = doc.addPicture(str(path))
    frame = Frame(width=f"{width}cm", height="8.4cm", anchortype="as-char")
    frame.addElement(Image(href=href))
    p.addElement(frame)
    doc.text.addElement(p)
    para(caption, "CaptionBeginner")


def flow_details(title, source, why):
    """Return beginner-friendly input/transform/output/scenario text."""
    text = (title + " " + source).lower()
    if "variable" in text:
        return (
            "values such as a number, text, or True/False",
            "assign each value to a readable variable name",
            "named variables that later lines can reuse",
            "at the beginning of a program when values need labels",
        )
    if "function" in text:
        return (
            "function arguments such as a and b",
            "run the indented instructions inside the function",
            "the value returned by return",
            "when the same operation may be needed many times",
        )
    if "condition" in text or "missing" in text:
        return (
            "a value that may be valid, missing, or None",
            "test a condition and choose one branch",
            "a missing-data action or a normal-data action",
            "when the program must not treat missing data as zero",
        )
    if "loop" in text or "csv" in text or "skipping" in text or "reading" in text:
        return (
            "a collection of rows, columns, or values",
            "visit each item once and process or skip it",
            "processed rows or a smaller collection of valid rows",
            "when a large dataset contains many repeated records",
        )
    if "dictionary" in text or "json" in text or "config" in text:
        return (
            "a JSON object containing named keys and values",
            "validate it or look up the fields described by the keys",
            "a usable configuration value or mapped source field",
            "when two systems use different names for the same concept",
        )
    if "async" in text or "await" in text:
        return (
            "a URL and a request that may take time",
            "wait for the network without blocking unrelated server work",
            "the HTTP response converted to JSON",
            "when the adapter contacts the source API or database",
        )
    if "sql" in text or "table" in text or "database" in text:
        return (
            "database tables, filters, and time boundaries",
            "select, filter, sort, insert, or aggregate matching rows",
            "rows, counts, or bucket summaries returned by the database",
            "when the project reads cached or datasource time-series data",
        )
    if "entity" in text or "ingest" in text or "archive" in text:
        return (
            "CSV columns, timestamps, values, or HTTP query parameters",
            "map source identifiers, validate data, and convert it to rows/JSON",
            "stable entity records or a paginated archive response",
            "when HLT source data is loaded or served over HTTP",
        )
    if "fingerprint" in text:
        return (
            "the resolved configuration object",
            "serialize it consistently and calculate a SHA-256 digest",
            "a short configuration fingerprint used in dataset_key",
            "when a changed config must receive a fresh cache namespace",
        )
    if "query" in text or "route" in text or "hydrate" in text or "request" in text:
        return (
            "a SeriesQuery containing dataset, entities, range, and resolution",
            "validate the request, find uncovered ranges, and delegate to the adapter",
            "a CanonicalSeries response with points and evidence metadata",
            "when the user selects entities and presses Go or changes the range",
        )
    if "cache" in text:
        return (
            "fetched samples, entity IDs, measures, and a dataset namespace",
            "store raw rows, coverage intervals, or rollup buckets conflict-safely",
            "reusable cached data without repeated source requests",
            "when the first query hydrates data or a later query reuses it",
        )
    if "tier" in text or "rollup" in text:
        return (
            "time span, pixel width, points per pixel, and configured levels",
            "calculate target seconds per output point and compare levels",
            "RAW or the selected bucket size such as 60s, 300s, or 3600s",
            "when zoom level changes how much detail the screen can show",
        )
    if "downsample" in text:
        return (
            "a selected payload and a display point budget",
            "run RAW, M4, LTTB, or MINMAXLTTB selection",
            "a smaller display payload that preserves useful visual features",
            "when many data points must fit inside a limited chart width",
        )
    if "gap" in text or "preserv" in text:
        return (
            "ordered timestamps and an expected sampling interval",
            "compare timestamp differences and mark unusually large gaps",
            "gap intervals in the response and null values in the renderer",
            "when the source skipped samples or a channel was offline",
        )
    if "dense" in text or "bucket" in text:
        return (
            "all cached rows in a range and a row_cap threshold",
            "aggregate every row into time buckets using SQL min/max/avg/count",
            "a full-range bucketed response instead of a truncated prefix",
            "when a requested range contains more rows than row_cap",
        )
    if "render" in text or "heatmap" in text or "histogram" in text or "frontend" in text:
        return (
            "canonical points, gaps, quality, or matrix values",
            "align values and draw chart, heatmap, or histogram elements",
            "a visual panel with tooltips and preserved missingness",
            "when the browser turns the backend response into evidence for the user",
        )
    return (
        "the values or objects shown in the code snippet",
        "perform the operations described by the individual lines",
        "the result or side effect described by the design reason",
        "when this module performs its named responsibility",
    )


def related_figure(title, source):
    text = (title + " " + source).lower()
    if "config" in text or "contract" in text:
        return "09_api_contract_translation.png — source fields become canonical fields."
    if "query" in text or "route" in text or "hydrate" in text or "request" in text:
        return "07_query_pipeline.png — request, hydration, tier selection, and response."
    if "cache" in text:
        return "10_cache_layers.png — raw samples, coverage, and rollups."
    if "rollup" in text or "tier" in text:
        return "03_rollup_buckets.png / 04_tier_selection.png — bucket contents and tier choice."
    if "downsample" in text:
        return "02_downsampling.png — RAW, M4, and LTTB display reduction."
    if "gap" in text or "missing" in text or "preserv" in text:
        return "01_raw_series.png / 05_gap_preservation.png — source gap and visible line break."
    if "dense" in text or "bucket" in text or "sql" in text:
        return "08_no_data_loss.png — full-range SQL bucket aggregation."
    if "frontend" in text or "render" in text or "heatmap" in text or "histogram" in text:
        return "11_frontend_panels.png — one response feeds three views."
    if "ingest" in text or "archive" in text or "entity" in text:
        return "06_architecture.png — source ingestion and datasource boundary."
    return "12_code_to_behavior.png — code input, backend behavior, and visible evidence."


def lesson(title, source, lines, why, say_text=None):
    """Add a code lesson, line explanations, I/O flow, reason, and figure link."""
    heading(title, 2)
    para("Source: " + source, "CodeSource")
    for number, (code, explanation) in enumerate(lines, 1):
        code_line(f"{number:02d}  {code}")
        para(f"Line {number}: {explanation}", "LineExplain")
    input_text, transform_text, output_text, scenario_text = flow_details(title, source, why)
    para("INPUT: " + input_text, "FlowBeginner")
    para("TRANSFORMATION: " + transform_text, "FlowBeginner")
    para("OUTPUT: " + output_text, "FlowBeginner")
    para("SCENARIO: " + scenario_text, "FlowBeginner")
    para("RELATED FIGURE: " + related_figure(title, source), "FlowBeginner")
    para("WHY THIS EXISTS: " + why, "WhyExplain")
    if say_text:
        para("PRESENTATION SENTENCE: " + say_text, "SayExplain")


# Cover and beginner promise
doc.text.addElement(H(text="Anaviz — Beginner Code Guide", outlinelevel="0", stylename="TitleBeginner"))
para("Line-by-line explanations, design reasons, and presentation sentences", "SubtitleBeginner")
para("For someone new to programming", "SubtitleBeginner")
para("This guide deliberately uses small snippets. It does not expect you to understand every library. First learn what each line means, then learn why that line is useful in Anaviz.", "SayExplain")
heading("How to use this guide", 1)
bullet("Read the beginner syntax section first.")
bullet("For each project snippet, read the code line, then its green explanation.")
bullet("Read the WHY THIS EXISTS paragraph to understand the design choice.")
bullet("Use the PRESENTATION SENTENCE as your speaking line tomorrow.")
bullet("You do not need to memorize syntax. Memorize the flow and the reason.")
heading("The project in one simple sentence", 1)
para("Anaviz receives time-series data from an HTTP API, stores requested data in a generic cache, chooses a useful resolution, and draws a chart without hiding gaps or important data evidence.", "SayExplain")
heading("Three layers", 1)
bullet("HLT is the system being monitored.")
bullet("p-beat is the heartbeat/rate measurement from HLT nodes.")
bullet("Anaviz is the software that visualizes those measurements.")

# Programming basics
heading("1. Programming basics before reading Anaviz", 1)
para("A program is a list of instructions. Python, JavaScript, and SQL use different punctuation, but the basic ideas are similar: store information, make decisions, repeat work, call functions, and return results.")
lesson("Variables: giving a value a name", "beginner Python", [
    ("count = 5", "The name count is created and the number 5 is stored in it."),
    ("name = \"HLT\"", "The name name stores text. Text is written inside quotation marks."),
    ("is_ready = True", "The name is_ready stores a Boolean value: True or False."),
], "Names make values easier to use later. Without a name, the program would have to repeat the value everywhere.", "A variable is simply a labeled box that stores information.")
lesson("Functions: reusable instructions", "beginner Python", [
    ("def add(a, b):", "def starts a function definition. add is the function name. a and b are inputs."),
    ("    total = a + b", "The indented line runs inside the function and adds the two inputs."),
    ("    return total", "return sends the answer back to the code that called the function."),
    ("answer = add(2, 3)", "The function is called with 2 and 3, and the returned result is stored in answer."),
], "Functions prevent repeated code. In Anaviz, functions separate tasks such as parsing timestamps, finding gaps, and selecting tiers.", "A function is a named mini-machine: inputs enter, work happens, and an output comes back.")
lesson("Conditions: making a decision", "beginner Python", [
    ("if value is None:", "if asks whether a condition is true. None means that no value exists."),
    ("    show_missing_message()", "The indented line runs only when the condition is true."),
    ("else:", "else describes the other case: the condition was false."),
    ("    draw_value(value)", "This line runs when a real value exists."),
], "Programs must treat missing data differently from valid data. This is essential for honest visualization.", "The program asks a question before deciding what to do.")
lesson("Loops: repeating work", "beginner Python", [
    ("for row in rows:", "for takes one item at a time from rows and calls it row."),
    ("    save(row)", "The indented instruction runs once for each item."),
], "The datasource contains many rows, so the same operation must be performed repeatedly without writing the same line thousands of times.", "A loop is the program saying: do this for every item in the collection.")
lesson("Dictionaries and JSON", "beginner Python / JSON", [
    ("config = {\"base_url\": \"http://source\"}", "A dictionary stores information as key and value pairs."),
    ("config[\"base_url\"]", "The key base_url is used to retrieve its value."),
    ("{\"t\": [1, 2], \"value\": [4, 5]}", "This JSON-like object contains two arrays: timestamps and values."),
], "APIs exchange structured data. Configuration and HTTP responses are naturally represented as key-value objects.", "JSON is a labeled package of data that different programs can exchange.")
lesson("async and await", "beginner Python", [
    ("async def fetch_data():", "async marks a function that may wait for network or database work."),
    ("    response = await client.get(url)", "await waits for the HTTP response without pretending it is already available."),
    ("    return response.json()", "After the response arrives, its JSON body is returned."),
], "Network requests are slow compared with normal calculations. Async code lets the server handle other work while waiting.", "await means: pause this task until the network answer arrives, then continue.")
lesson("SQL basics", "beginner SQL", [
    ("SELECT ts, value", "SELECT chooses which columns to return."),
    ("FROM eventhistory", "FROM chooses the table."),
    ("WHERE element_id = 900000", "WHERE filters rows to one channel."),
    ("ORDER BY ts", "ORDER BY sorts the result by time."),
], "The database contains many channels and timestamps. Filtering and ordering lets the project retrieve exactly the requested series.", "SQL is a language for asking the database a precise question.")

# Data/source code
heading("2. Source data: from CSV to database", 1)
para("The datasource package is source-specific. It knows that the HLT data arrives as CSV and that HLT columns represent channels. The generic project does not contain this knowledge.")
lesson("Assigning entity IDs", "datasource/anaviz_datasource/ingest_hlt.py", [
    ("ELEMENT_ID_OFFSET = 900_000", "A constant is a value whose meaning is named. Here it defines the starting ID."),
    ("for i, col in enumerate(columns):", "The loop visits every CSV column. enumerate gives both position i and column name col."),
    ("    eid = ELEMENT_ID_OFFSET + i", "The position is added to the starting number to create a unique entity ID."),
    ("    col_to_eid[col] = eid", "The dictionary remembers which source column maps to which entity ID."),
], "The rest of the system needs stable identifiers. The mapping also prevents the frontend from depending on long hardware names.", "The importer turns source columns into stable entities that the API can return.")
lesson("Reading a huge CSV safely", "datasource/anaviz_datasource/ingest_hlt.py", [
    ("chunksize = 5000", "This sets the number of CSV rows loaded in one piece."),
    ("reader = pd.read_csv(path, chunksize=chunksize)", "Pandas creates a reader that provides one chunk at a time."),
    ("for chunk_n, df in enumerate(reader):", "The loop processes each chunk instead of loading the entire file."),
    ("    df.index = pd.to_datetime(df.index, utc=True)", "The timestamp index is converted into a consistent UTC time format."),
], "The files are gigabytes in size. Chunking controls memory usage and makes ingestion practical.", "We process a large file like a stack of small pages instead of opening the whole book at once.")
lesson("Skipping missing CSV cells", "datasource/anaviz_datasource/ingest_hlt.py", [
    ("for ts, val in zip(df.index, col_data):", "zip pairs each timestamp with the value at that timestamp."),
    ("    if isinstance(val, float) and np.isnan(val):", "This checks whether the value is a floating-point NaN."),
    ("        continue", "continue skips this missing cell and moves to the next one."),
    ("    buf.write(f\"{eid},{ts.isoformat()},{val:.8g},0\\n\")", "Valid data is formatted as one database row in the temporary buffer."),
], "A missing cell is not a measured zero, so it must not become a fake zero in the database.", "The importer stores real observations and leaves missing observations absent.")
lesson("Safe repeated ingestion", "datasource/anaviz_datasource/ingest_hlt.py", [
    ("INSERT INTO eventhistory (...) SELECT ...", "Rows from the temporary staging table are copied into the real table."),
    ("ON CONFLICT (element_id, ts)", "The database checks the unique channel-and-time key."),
    ("DO UPDATE SET value = EXCLUDED.value", "If the key already exists, the new value updates the old row instead of duplicating it."),
], "Large ingestion can be interrupted or repeated. Conflict handling makes reruns safe and idempotent.", "Idempotent means running the same import again does not create duplicate measurements.")
image("06_architecture.png", "Figure 1 — The source-download and datasource side of the project architecture.")

# API/config
heading("3. The HTTP API and config.json", 1)
para("The archive API exposes source data. config.json tells the generic adapter how to call it. The adapter then converts the response into a common format.")
lesson("A simple archive endpoint", "datasource/anaviz_datasource/archive_server.py", [
    ("@app.get(\"/archive/eventhistory\")", "This decorator registers a URL. A GET request to this path calls the function below it."),
    ("async def eventhistory(element_id: int, t0: str, t1: str):", "The function receives a channel ID and two time boundaries. Type hints describe expected kinds of values."),
    ("    rows = await query_database(element_id, t0, t1)", "The server waits for the database query to return rows."),
    ("    return {\"t\": ..., \"value\": ..., \"quality_flag\": ...}", "The function sends a JSON response containing parallel arrays."),
], "The datasource should expose a stable HTTP boundary so the project does not need direct database access.", "The archive service answers a focused question: give me this channel between these two times.")
lesson("A small config mapping", "user config.json", [
    ("\"base_url\": \"http://datasource:9000\"", "This tells the adapter where the source service lives on the container network."),
    ("\"path\": \"/archive/eventhistory\"", "This is the relative endpoint used for time-series requests."),
    ("\"shape\": \"columnar\"", "This says the response has parallel arrays instead of one object per row."),
    ("\"timestamp\": \"t\"", "This says the source timestamp array is named t."),
    ("\"value_path\": \"value\"", "This says the measurement array is named value."),
], "The adapter needs instructions because different APIs use different names and response shapes.", "config.json is the bridge: it describes the source without adding source-specific code to the frontend.")
lesson("Config validation", "server/api/config.py", [
    ("class DatasetConfig(BaseModel):", "class defines a structured model; BaseModel validates its fields."),
    ("    dataset: DatasetInfo", "The configuration must contain dataset information."),
    ("    source: HttpSource", "The configuration must contain a valid HTTP source description."),
    ("    endpoints: Endpoints", "The configuration must describe entity, extent, and series endpoints."),
    ("    mapping: Mapping", "The configuration must explain how source fields map to canonical fields."),
], "Rejecting an invalid configuration early is safer than allowing a partially understood source into the cache.", "Validation turns a loose JSON file into a predictable contract.")
lesson("Configuration fingerprint", "server/api/config.py", [
    ("canonical = json.dumps(config, sort_keys=True)", "The configuration is converted to a stable, consistently ordered string."),
    ("digest = hashlib.sha256(canonical.encode()).hexdigest()", "SHA-256 creates a long fingerprint from that configuration text."),
    ("return digest[:16]", "The project keeps a short readable part of the fingerprint."),
], "If field mappings change, old cached values may no longer mean the same thing. A new fingerprint gives the new configuration a new cache namespace.", "The fingerprint is like a version label for the meaning of the cached data.")
image("09_api_contract_translation.png", "Figure 2 — config.json translates a source API into a canonical API.")

# Contracts
heading("4. Canonical contracts: common language", 1)
para("A contract is an agreed shape of data. The source can be different, but every adapter returns the same kind of request and response to the frontend.")
lesson("The query request", "server/api/contracts.py", [
    ("class SeriesQuery(BaseModel):", "This defines the structure of a series request."),
    ("    dataset_id: str = \"default\"", "The request identifies which registered dataset is being queried."),
    ("    entity_ids: list[str]", "The list contains the selected channels or entities."),
    ("    range: TimeRange", "The request includes start and end times."),
    ("    resolution: ResolutionRequest", "The request includes a screen-based output budget."),
    ("    downsampling: Literal[\"LTTB\", \"M4\", \"RAW\"]", "Only supported algorithm names are accepted."),
], "A predictable request lets the route and adapter work for every configured dataset.", "The frontend asks for data in general terms: entities, time range, resolution, and algorithm.")
lesson("The response includes evidence", "server/api/contracts.py", [
    ("class CanonicalSeries(BaseModel):", "This defines one returned series."),
    ("    points: SeriesPoints", "points contains timestamps and values, plus optional bands."),
    ("    gaps: list[list[float]]", "gaps records intervals where the source had missing coverage."),
    ("    fidelity: dict[str, Any]", "fidelity records how the result was produced."),
    ("    metrics: QueryMetrics", "metrics records counts and timing information."),
], "A line by itself cannot tell the user whether it was raw, aggregated, or affected by missing data. Evidence fields provide that context.", "The API returns the answer and an explanation of the answer.")

# Query pipeline
heading("5. Query pipeline, explained slowly", 1)
image("07_query_pipeline.png", "Figure 3 — The query pipeline from browser request to canonical response.")
lesson("The browser sends a request", "frontend/app.js", [
    ("const body = {", "const creates a value that the JavaScript code will not reassign."),
    ("  dataset_id: datasetId,", "The selected dataset is included."),
    ("  entity_ids: selected.map(e => String(e.entity_id)),", "The selected entities are converted into the list expected by the API."),
    ("  range: { start: view.t0, end: view.t1 },", "The visible time window is included."),
    ("  downsampling: algo,", "The user-selected display algorithm is included."),
    ("};", "The closing brace ends the JavaScript object."),
], "The browser should describe the desired query, not implement database or caching decisions.", "The UI sends a request description; the backend decides how to fulfill it.")
lesson("The route chooses an adapter", "server/app.py", [
    ("@app.post(\"/api/query\")", "This registers the POST endpoint used by the frontend."),
    ("async def canonical_query(request: SeriesQuery):", "The route receives a validated SeriesQuery object."),
    ("    adapter = _get_dataset_adapter(request.dataset_id)", "The dataset ID selects the correct configured adapter."),
    ("    return await adapter.query(request)", "The route delegates the actual work and returns the adapter response."),
], "Keeping the route thin prevents server/app.py from becoming a collection of source-specific rules.", "The route is the receptionist: it sends the request to the correct specialist.")
lesson("Hydrate uncovered ranges", "server/api/configurable.py", [
    ("covered = await cache.coverage(entity, measure, start, end)", "Ask the cache which parts of the requested interval were already checked."),
    ("gaps = coverage_gaps(start, end, covered)", "Calculate the portions that are not covered."),
    ("for a, b in gaps:", "Repeat for each uncovered time interval."),
    ("    rows = await fetch_source(entity, measure, a, b)", "Fetch only that missing interval from the configured HTTP source."),
    ("    await cache.store(entity, measure, rows)", "Save the fetched rows so future requests can reuse them."),
], "On-demand hydration avoids downloading and caching an entire dataset before the user asks for it.", "The first query fills the cache; repeated queries reuse it.")

# Cache details
heading("6. Cache: three tables, three jobs", 1)
image("10_cache_layers.png", "Figure 4 — cache_series, cache_coverage, and optional cache_rollup.")
lesson("Raw sample table", "db/cache_schema.sql", [
    ("CREATE TABLE cache_series (", "CREATE TABLE asks the database to create a table."),
    ("    dataset_key TEXT NOT NULL,", "dataset_key identifies the dataset and configuration namespace."),
    ("    entity_id TEXT NOT NULL,", "entity_id identifies the channel or entity."),
    ("    ts TIMESTAMPTZ NOT NULL,", "ts stores a timestamp; it cannot be missing."),
    ("    value DOUBLE PRECISION NOT NULL,", "value stores a numeric measurement."),
    ("    quality SMALLINT NOT NULL DEFAULT 0,", "quality stores a small quality flag and defaults to zero."),
    ("    PRIMARY KEY (dataset_key, entity_id, measure_id, ts)", "The primary key prevents duplicate samples for the same namespace, entity, measure, and time."),
    (")", "The closing parenthesis finishes the table definition."),
], "The generic fixed schema supports many datasets without creating HLT-specific tables in the project.", "cache_series is the project’s notebook of raw samples it has already fetched.")
lesson("Coverage table meaning", "db/cache_schema.sql", [
    ("status TEXT CHECK (status IN ('queried', 'empty'))", "The status records whether a range was checked or known to contain no samples."),
    ("t_min TIMESTAMPTZ", "t_min is the beginning of a covered interval."),
    ("t_max TIMESTAMPTZ", "t_max is the end of a covered interval."),
], "Coverage avoids repeated source requests, but it does not claim that every timestamp inside a queried interval has a value. Internal gaps can still exist.", "Queried means we checked the source; it does not mean every second contained a sample.")

# Rollup/downsampling
heading("7. Rollups and downsampling", 1)
image("03_rollup_buckets.png", "Figure 5 — One bucket preserves first, min, max, last, average, count, and quality.")
lesson("Choosing a tier", "server/downsample/rollup.py", [
    ("target = span_s / (pixel_width * points_per_pixel)", "target estimates how many seconds one display point represents."),
    ("if target < levels[0] * 4:", "If the range is too small to benefit, do not aggregate."),
    ("    return \"raw\"", "Return the original cached samples."),
    ("for level in sorted(levels):", "Check configured bucket levels from smallest to largest."),
    ("    if level <= target:", "A level is useful when its bucket is not larger than the target."),
    ("        chosen = level", "Remember the best useful level found so far."),
    ("return chosen", "Return raw or the selected bucket size."),
], "The screen resolution should influence the data resolution. A small query needs detail; a large query needs aggregation.", "The same dataset can be raw when zoomed in and bucketed when zoomed out.")
image("04_tier_selection.png", "Figure 6 — Short spans use RAW; longer spans use configured bucket tiers.")
lesson("Downsampling dispatch", "server/downsample/__init__.py", [
    ("n = len(payload[\"t\"])", "Count the timestamps in the selected payload."),
    ("if n == 0 or algo == \"RAW\":", "If there is no data or RAW was requested, do not reduce it."),
    ("    return payload, 0.0", "Return the original payload and zero processing time."),
    ("if algo == \"M4\":", "Choose the M4 algorithm when requested."),
    ("    idx, ts, vals = m4_series(...)", "M4 selects representative first/min/max/last points."),
    ("else:", "For another supported algorithm, use its implementation."),
    ("    idx = lttb_indices(...)", "LTTB selects points that preserve visual shape."),
], "The dispatcher keeps algorithm choice in one place and lets the frontend request a supported strategy.", "Downsampling changes the display payload, not the source data stored in the cache.")
image("02_downsampling.png", "Figure 7 — RAW, M4, and LTTB use different numbers of representative points.")

# Fidelity
heading("8. Missing values and gaps", 1)
image("01_raw_series.png", "Figure 8 — The raw series contains a source gap.")
lesson("Detecting a gap", "server/api/common.py", [
    ("deltas = np.diff(timestamps)", "Calculate the difference between each timestamp and the next timestamp."),
    ("expected = median_positive_delta", "Estimate the normal sampling interval when one is not configured."),
    ("threshold = expected * 1.5", "Define how much larger than normal a difference must be to count as a gap."),
    ("if delta > threshold:", "Ask whether the current difference is unusually large."),
    ("    gaps.append([start, end])", "Record the beginning and end of the missing interval."),
], "A chart should show the difference between a quiet signal and no data. Timestamp gaps provide that evidence.", "The program detects missing time by noticing that the next sample arrived much later than expected.")
lesson("Preserving the gap in JavaScript", "frontend/utils.js", [
    ("const inGap = x > gapStart && x < gapEnd;", "Check whether the display timestamp x lies inside a declared gap."),
    ("if (inGap) {", "Start the missing-data case."),
    ("    vals[i] = null;", "Store null instead of inventing a value."),
    ("} else {", "Otherwise, the timestamp is not inside the gap."),
    ("    vals[i] = displayValue;", "Use the observed or display-aligned value."),
], "Null tells the chart renderer to break the line. It prevents an attractive but false connection between two observations.", "Missing is not zero, and missing is not an interpolated measurement.")
image("05_gap_preservation.png", "Figure 9 — The correct chart breaks; the wrong chart interpolates across missing data.")

# Dense/no data loss
heading("9. Dense ranges: why the chart is not truncated", 1)
image("08_no_data_loss.png", "Figure 10 — Full SQL bucket aggregation avoids stopping at row_cap.")
lesson("Detecting a dense range", "server/api/configurable.py", [
    ("total = await cache.count(entity, measure, start, end)", "Count all cached rows in the requested range."),
    ("if total > self.config.row_cap:", "Choose the dense-data path when the count is above the configured reading cap."),
    ("bucket_s = span_seconds / pixel_budget", "Choose buckets based on time span and the number of pixels available."),
    ("data = await cache.read_bucketed(bucket_s, ...)", "Ask the database to aggregate the complete range into time buckets."),
], "Returning only the first row_cap rows would hide the end of the range. Aggregation summarizes all rows instead of truncating the visualization.", "row_cap controls how a dense range is read; it does not mean the rest of the time range is thrown away.")
lesson("SQL bucket summary", "server/api/configurable.py", [
    ("SELECT min(v), max(v), avg(v), count(*)", "Calculate the range, average, and number of raw samples in each bucket."),
    ("FROM cache_series", "Use all raw cached samples as the source."),
    ("WHERE dataset_key = ...", "Keep one dataset configuration isolated from another."),
    ("GROUP BY time_bucket", "Combine rows that belong to the same time bucket."),
    ("ORDER BY time_bucket", "Return buckets in chronological order."),
], "The chart receives a compact representation, but every raw row contributes to a bucket and the important extrema are preserved.", "The system summarizes the full range rather than returning a truncated prefix.")

# Frontend
heading("10. Frontend: how the browser displays the result", 1)
image("11_frontend_panels.png", "Figure 11 — One canonical response feeds the chart, heatmap, and histogram.")
lesson("Rendering the line chart", "frontend/chart.js", [
    ("const series = [{}];", "Create the list of series; the first empty item is used by uPlot for the x-axis."),
    ("selected.forEach((entity, i) => {", "Create one visual series for each selected entity."),
    ("    series.push({ label: entity.label, stroke: color });", "Add a labeled colored line for the entity."),
    ("});", "Finish the loop over selected entities."),
    ("plot = new uPlot(options, data, container);", "Create the uPlot chart using options, data, and the HTML container."),
], "The chart module should focus on rendering. It should not know how the database or HTTP adapter obtained the values.", "The browser receives canonical data and turns it into visual lines and tooltips.")
lesson("Heatmap missing cells", "frontend/heatmap.js", [
    ("const score = row?.[column];", "Read the score for one entity and one time column."),
    ("ctx.fillStyle = score == null ? missingColor : scoreToColor(score);", "Use a missing color for null, otherwise convert the score to a color."),
    ("ctx.fillRect(x, y, width, height);", "Paint that one matrix cell on the Canvas."),
], "The heatmap must distinguish no sample from a low score. Otherwise the color would communicate the wrong meaning.", "A gray or empty cell means no source sample, not a score of zero.")
lesson("Histogram reuse", "frontend/histogram.js", [
    ("function updateHistograms(selected, lastRenderData, bins) {", "Define a function that receives already-selected entities and already-fetched data."),
    ("    makeBins(lastRenderData, bins);", "Group values into ranges for the histogram."),
    ("    drawHistogram();", "Draw the distribution on the page."),
    ("}", "Close the function."),
], "Using lastRenderData avoids another HTTP request just to show a distribution of data already on the screen.", "The histogram reuses the chart response instead of fetching the same data again.")

# New code-to-behavior image
heading("11. New picture: code → backend behavior → evidence", 1)
image("12_code_to_behavior.png", "Figure 12 — Every snippet should be explained through its input, behavior, and visible result.")
para("This is the best method for explaining code to a non-programmer audience:")
bullet("Input: what information enters the function?")
bullet("Behavior: what does the function decide or transform?")
bullet("Output: what does the user see or what guarantee is produced?")
lesson("The whole pipeline in one line", "presentation summary", [
    ("request -> validate -> hydrate", "The request is checked and missing data is fetched."),
    ("-> choose tier -> downsample", "The backend chooses the right resolution and display size."),
    ("-> response -> chart + heatmap + histogram", "The canonical result drives all frontend views."),
], "A short pipeline helps the audience understand how many small functions fit together into one user action.", "The code is modular, but the user experiences one continuous flow from selecting data to seeing evidence.")

# Presentation prep
heading("12. What to say in the presentation", 1)
heading("Opening", 2)
para("Anaviz is a dataset-independent time-series visualization system. The HLT dataset is the example, but the application communicates with a source through config.json instead of hard-coding HLT into the frontend.", "SayExplain")
heading("Architecture", 2)
para("The datasource is the source of truth. The project is a separate generic visualization service with its own cache. They communicate through HTTP, so project cache operations cannot destroy the real dataset.", "SayExplain")
heading("Code explanation method", 2)
para("I will show a small code snippet, explain the input, explain the operation, explain the output, and then connect it to the user-visible chart behavior.", "SayExplain")
heading("Performance", 2)
para("The system uses on-demand caching, optional rollup tiers, pixel-budget downsampling, and SQL bucket aggregation for dense ranges. It avoids sending millions of points directly to the browser.", "SayExplain")
heading("Data quality", 2)
para("Missing data remains visible. The system does not turn missing values into zero and does not draw a line across a declared gap.", "SayExplain")
heading("Closing", 2)
para("The important result is not only a fast chart. It is a chart that explains its data: its resolution, gaps, quality, sample counts, aggregation, and provenance.", "SayExplain")

heading("13. Short questions and answers", 1)
heading("What is HLT?", 2)
para("HLT is the High Level Trigger computing system in the ATLAS experiment.")
heading("What is p-beat?", 2)
para("P-beat is the periodic heartbeat or rate monitoring data collected from HLT nodes. HLT is the system; p-beat is the measurement.")
heading("Does config.json contain code?", 2)
para("No. It contains data describing endpoints, parameters, field paths, formats, and performance choices.")
heading("Does downsampling delete the source data?", 2)
para("No. It reduces the display response. Raw samples remain in the project cache and important extrema/counts are preserved in bucketed responses.")
heading("Why not fill a gap with zero?", 2)
para("Zero is a real measurement. Missing means there was no measurement, so replacing it with zero would create false information.")
heading("Why two databases?", 2)
para("The datasource database owns the real HLT data. The project database owns a re-creatable query cache. Separation protects the source data.")

heading("14. Final beginner checklist", 1)
bullet("A variable stores a value under a name.")
bullet("A function is reusable instructions with inputs and an optional output.")
bullet("A loop repeats work for many items.")
bullet("An if statement makes a decision.")
bullet("A dictionary/JSON object stores named fields.")
bullet("async and await handle work that waits for a network or database.")
bullet("SQL asks the database for selected rows and columns.")
bullet("The datasource owns raw HLT data.")
bullet("config.json describes how to call the datasource.")
bullet("The adapter translates source data into a canonical contract.")
bullet("The cache avoids repeating requests.")
bullet("Rollups summarize time buckets.")
bullet("Downsampling fits the response to the display.")
bullet("Gaps and nulls preserve missingness.")
bullet("Fidelity metadata explains the displayed result.")
para("You do not need to claim that you wrote every algorithm from scratch. You can accurately say that you understand the data flow, the responsibilities of each module, and the design reasons behind caching, rollups, downsampling, and gap preservation.", "SayExplain")

# Save
doc.save(str(OUT))
print(f"Saved {OUT}")
