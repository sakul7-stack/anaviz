/**
 * app.js — Main application coordinator.
 *
 * Responsibilities:
 *  - Dataset discovery and loading
 *  - Entity selection
 *  - Date range navigation
 *  - Data fetching and transformation
 *  - Panel coordination (chart, heatmap, histogram)
 *  - Event handlers for UI controls
 */
import {
  $, COLORS, MAX_SERIES, chartPixelWidth,
  toLocalISO, parseLocalISO,
  api, apiPost,
  seriesResponseToRenderer, matrixResponseToHeatmap,
  buildSharedTimeAxis, interpTo, alignExact,
} from "./utils.js";

import {
  plot, lastRenderData,
  destroyPlot, chartUpdate, buildLegend,
  setZoom, clearZoom, watchChartResize,
  setChartCursor, setHoverSync, setChartDrawSync,
  setTooltipEnricher, getValuesAtTime,
} from "./chart.js";

import {
  initHeatmap, updateHeatmap, highlightColumn,
  redrawHeatmap, setHeatmapTooltipExtra,
} from "./heatmap.js";

import {
  updateHistograms, clearHistograms,
  setHistogramHover, clearHistogramHover, binInfoLine,
} from "./histogram.js";

// ── Application state ────────────────────────────────────────────────────

let entities = [];           // all entities from the current dataset
let selected = [];           // currently selected entities
let datasetId = null;        // active dataset ID
let measureId = "value";     // active measure ID
let view = { t0: null, t1: null }; // current time range
let dataExtent = null;       // full dataset time extent { t0, t1 }
let lastResults = [];        // raw API results from last fetch (for re-rendering)

// UI helpers
const isHeatmapVisible = () => $("#showHeatmap").checked;
const isHistogramVisible = () => $("#showHist").checked;
const getHistogramBinCount = () =>
  Math.min(100, Math.max(4, Math.round(Number($("#histBins")?.value) || 24)));
const getInterpolationMode = () => $("#interpMode")?.value || "linear";

// ── Dataset loading ──────────────────────────────────────────────────────

async function loadDataset(id) {
  try {
    const datasets = await api("/api/datasets");
    if (!datasets?.length) throw new Error("no datasets configured");

    datasetId = id || datasets[0].id;
    const base = `/api/datasets/${encodeURIComponent(datasetId)}`;

    // Fetch entities, schema, and extent in parallel
    const [entityPage, schema, extent] = await Promise.all([
      api(`${base}/entities`, { limit: 10000 }),
      api(`${base}/schema`),
      api(`${base}/extent`),
    ]);

    measureId = schema?.measures?.[0]?.id || "value";
    entities = (entityPage.items || []).map((item) => ({
      entity_id: item.id,
      label: item.label,
      name: item.label,
      attributes: item.attributes || {},
    }));

    dataExtent = { t0: extent.start, t1: extent.end };

    // Update page title
    document.title = `${schema.label} · Time-series Explorer`;
    const heading = document.querySelector("header h1");
    if (heading) heading.textContent = schema.label.toUpperCase();

    // Show entities
    if (!entities.length) {
      $("#entitylist").innerHTML =
        '<div style="color:var(--dim);padding:8px">no entities</div>';
      return;
    }

    renderEntityList("");

    // Default to last 24 hours
    $("#dt0").value = toLocalISO(new Date((extent.end - 86400) * 1000));
    $("#dt1").value = toLocalISO(new Date(extent.end * 1000));
  } catch (err) {
    console.error("dataset load failed", err);
    $("#entitylist").innerHTML =
      `<div style="color:#b00;padding:8px">failed: ${err.message}</div>`;
  }
}

async function loadDataSources() {
  try {
    const [datasets, configs] = await Promise.all([
      api("/api/datasets"),
      api("/api/configs"),
    ]);

    // Populate dataset selector
    const select = $("#datasetSelect");
    if (!select) return;
    const previous = select.value;
    select.innerHTML = "";

    (datasets || []).forEach((ds) => {
      const option = document.createElement("option");
      option.value = ds.id;
      option.textContent = ds.label;
      select.appendChild(option);
    });

    if (previous && datasets.some((d) => d.id === previous)) {
      select.value = previous;
    }

    renderConfigList(configs || []);

    if (datasets?.length) {
      await loadDataset(select.value || datasets[0].id);
    } else {
      showEmptyState();
    }
  } catch (err) {
    console.error("data sources failed", err);
  }
}

function showEmptyState() {
  const panel = $("#dsPanel");
  if (panel) panel.open = true;

  $("#entitylist").innerHTML = `
    <div class="empty-state">
      <div class="empty-title">No data source</div>
      <div class="empty-sub">
        Open <b>Data sources</b>, paste config.json, click <b>Register</b>.
      </div>
    </div>`;
  $("#legend").textContent = "Register a data source";
  $("#stats").textContent = "no data source";
}

// ── Config list ──────────────────────────────────────────────────────────

function renderConfigList(configs) {
  const list = $("#configList");
  if (!list) return;
  list.innerHTML = "";

  (configs || []).forEach((cfg) => {
    const row = document.createElement("div");
    row.className = "config-row";

    const info = document.createElement("span");
    info.className = "config-info";
    info.textContent = cfg.label;

    const removeBtn = document.createElement("button");
    removeBtn.className = "config-del";
    removeBtn.textContent = "✕";
    removeBtn.onclick = async () => {
      if (!confirm(`Remove "${cfg.label}"?`)) return;
      try {
        await api(`/api/configs/${encodeURIComponent(cfg.id)}`, {}, null, "DELETE");
        await loadDataSources();
      } catch (err) {
        $("#stats").textContent = `Failed: ${err.message}`;
      }
    };

    row.append(info, removeBtn);
    list.appendChild(row);
  });
}

// ── Entity list ──────────────────────────────────────────────────────────

function renderEntityList(filter) {
  const list = $("#entitylist");
  list.innerHTML = "";

  entities
    .filter((e) => e.name.toLowerCase().includes(filter.toLowerCase()))
    .forEach((entity) => {
      const label = document.createElement("label");

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.some((s) => s.entity_id === entity.entity_id);
      checkbox.onchange = () => toggleEntity(entity);

      const swatch = document.createElement("span");
      swatch.className = "legend-swatch";
      const idx = selected.findIndex((s) => s.entity_id === entity.entity_id);
      swatch.style.background = idx >= 0 ? COLORS[idx % COLORS.length] : "transparent";

      label.append(checkbox, swatch, document.createTextNode(entity.label || entity.name));
      list.appendChild(label);
    });
}

async function toggleEntity(entity) {
  const index = selected.findIndex((s) => s.entity_id === entity.entity_id);

  if (index >= 0) {
    selected.splice(index, 1);
  } else if (selected.length < MAX_SERIES) {
    selected.push(entity);
  } else {
    $("#stats").textContent = `Max ${MAX_SERIES} entities`;
  }

  renderEntityList($("#filter").value || "");
  buildLegend(selected);
}

// ── Navigation ───────────────────────────────────────────────────────────

let navTimer = null;
let navBurst = false;        // true while user is actively scrolling/zooming
let abortController = null;
let requestSequence = 0;
let statusPollTimer = null;

function startStatusPoll() {
  stopStatusPoll();
  statusPollTimer = setInterval(async () => {
    try {
      const status = await api("/api/status");
      if (status.refreshing?.length) {
        $("#stats").innerHTML =
          '<span class="stat-block refresh">⟳ refreshing</span>';
      }
    } catch { /* ignore transient errors */ }
  }, 800);
}

function stopStatusPoll() {
  if (statusPollTimer) {
    clearInterval(statusPollTimer);
    statusPollTimer = null;
  }
}

function navigateTo(start, end) {
  // Clamp to dataset extent
  let lo = dataExtent?.t0 ?? Math.min(start, view.t0 ?? start);
  let hi = dataExtent?.t1 ?? Math.max(end, view.t1 ?? end);
  if (hi <= lo) { lo = start; hi = end; }

  const span = Math.min(Math.max(end - start, 1), hi - lo);
  const center = (start + end) / 2;
  start = center - span / 2;
  end = center + span / 2;

  if (start < lo) { start = lo; end = lo + span; }
  if (end > hi) { end = hi; start = hi - span; }

  if (view.t0 === start && view.t1 === end) return;

  view = { t0: start, t1: end };
  setZoom(start, end);

  if (plot) plot.setScale("x", { min: start, max: end });
  $("#dt0").value = toLocalISO(new Date(start * 1000));
  $("#dt1").value = toLocalISO(new Date(end * 1000));

  // Debounce: only fetch data after movement settles
  navBurst = true;
  if (navTimer) clearTimeout(navTimer);
  navTimer = setTimeout(() => {
    navTimer = null;
    navBurst = false;
    fetchData();
  }, 180);
}

// ── Data assembly ────────────────────────────────────────────────────────

/**
 * Transform API results into the format the chart renderer expects.
 */
function buildChartData(results, interpMode) {
  const allTimes = buildSharedTimeAxis(results);
  const data = [allTimes];
  const bandByEntity = [];
  const entityMeta = [];

  const stats = {
    scanned: 0, rawTotal: 0, sent: 0, gapTotal: 0,
    dbMs: 0, dsMs: 0, resName: "raw", respAlgo: "M4", hasData: false,
  };

  for (const result of (Array.isArray(results) ? results : [])) {
    // Error or empty series
    if (!result || result.error || !result.series.t.length) {
      data.push(new Array(allTimes.length).fill(null));
      bandByEntity.push(false);
      entityMeta.push({
        observed: new Array(allTimes.length).fill(null),
        gaps: [],
      });
      continue;
    }

    const series = result.series;
    const gaps = result.gap_intervals || [];
    const hasBand = (result.algo === "LTTB" || result.algo === "MINMAXLTTB")
      && series.min !== undefined;

    // Interpolate values onto shared time axis
    data.push(interpTo(allTimes, series.t, series.avg, interpMode, gaps));
    if (hasBand) {
      data.push(interpTo(allTimes, series.min_t || series.t, series.min, interpMode, gaps));
      data.push(interpTo(allTimes, series.max_t || series.t, series.max, interpMode, gaps));
    }
    bandByEntity.push(hasBand);

    // Metadata for tooltip display
    entityMeta.push({
      observed: alignExact(allTimes, series.t, series.avg),
      quality: alignExact(allTimes, series.t, series.quality_flag || []),
      sampleCount: alignExact(allTimes, series.t, series.sample_count || []),
      gaps,
      expectedStep: result.expected_step_seconds,
      qualitySummary: result.quality_summary || {},
      dataFidelity: result.data_fidelity || {},
    });

    // Aggregate stats
    stats.gapTotal += gaps.length;
    stats.scanned += result.rows_scanned;
    stats.rawTotal += result.rows_raw;
    stats.sent += result.rows_returned;
    stats.dbMs = Math.max(stats.dbMs, result.query_ms);
    stats.dsMs = Math.max(stats.dsMs, result.ds_ms || 0);
    stats.resName = result.resolution;
    stats.respAlgo = result.algo || "M4";
    stats.hasData = true;
  }

  return { data, bandByEntity, entityMeta, stats };
}

function renderStatsBar(stats, results) {
  if (!stats.hasData) {
    $("#stats").textContent = "no data in range";
    return;
  }

  const failedCount = selected.length - (
    Array.isArray(results)
      ? results.filter((r) => r && !r.error).length
      : 0
  );
  const errorHtml = failedCount
    ? `<span class="stat-err"> ${failedCount} failed</span>`
    : "";

  const spanHours = ((view.t1 - view.t0) / 3600).toFixed(1);
  const items = [
    ["series", selected.length],
    ["range", `${spanHours}h`],
    ["tier", stats.resName],
    ["raw", stats.rawTotal.toLocaleString()],
    ["db", stats.scanned.toLocaleString()],
    ["out", stats.sent.toLocaleString()],
    ["gaps", stats.gapTotal],
    ["db", `${stats.dbMs}ms`],
    [stats.respAlgo, `${stats.dsMs}ms`],
  ];

  $("#stats").innerHTML = items
    .map(([label, value]) =>
      `<span class="stat-block">
        <span class="stat-label">${label}</span>${value}
      </span>`)
    .join("") + errorHtml;
}

function renderHistogramPanel(hasData) {
  const wrap = document.getElementById("histwrap");
  if (!isHistogramVisible()) {
    wrap.style.display = "none";
    return;
  }
  wrap.style.display = "";
  if (hasData && lastRenderData) {
    updateHistograms(selected, lastRenderData, getHistogramBinCount());
  } else {
    clearHistograms();
  }
}

// ── Main fetch ───────────────────────────────────────────────────────────

async function fetchData() {
  if (!selected.length || view.t0 === null) return;

  $("#stats").textContent = "Loading…";

  // Cancel previous request
  if (abortController) abortController.abort();
  abortController = new AbortController();
  const signal = abortController.signal;
  const myId = ++requestSequence;

  startStatusPoll();
  const algo = $("#algo").value;

  // Fetch series data
  const seriesPromise = apiPost("/api/query", {
    dataset_id: datasetId,
    entity_ids: selected.map((e) => String(e.entity_id)),
    measure_ids: [measureId],
    range: { start: view.t0, end: view.t1 },
    resolution: {
      strategy: "auto",
      pixel_width: chartPixelWidth(),
      points_per_pixel: 2,
    },
    downsampling: algo,
    filters: {},
  }, signal)
    .then(seriesResponseToRenderer)
    .catch(() => []);

  // Fetch heatmap data (only if visible and not during a scroll burst)
  const heatmapPromise = (isHeatmapVisible() && selected.length && !navBurst)
    ? seriesPromise.then(() => apiPost("/api/matrix", {
        dataset_id: datasetId,
        entity_ids: selected.map((e) => String(e.entity_id)),
        measure_id: measureId,
        range: { start: view.t0, end: view.t1 },
        transform: "temporal_rolling_zscore",
        pixel_width: Math.min(2000, Math.max(200,
          document.getElementById("heatmap")?.offsetWidth || 200)),
      }, signal).then(matrixResponseToHeatmap)).catch(() => null)
    : Promise.resolve(null);

  // Wait for series data
  const results = await seriesPromise;
  if (myId !== requestSequence) return;
  stopStatusPoll();

  // Store raw results for local re-rendering (e.g. interpolation change)
  lastResults = results;

  // Build and render chart
  const interpMode = getInterpolationMode();
  const { data, bandByEntity, entityMeta, stats } = buildChartData(results, interpMode);

  if (stats.hasData) {
    chartUpdate(data, selected, navigateTo,
      $("#perAxis").checked, bandByEntity, entityMeta, interpMode);
    buildLegend(selected);
  }

  renderStatsBar(stats, results);
  renderHistogramPanel(stats.hasData);

  // Update heatmap panel visibility
  const heatmapWrap = document.getElementById("heatmapwrap");
  const heatmapState = document.getElementById("heatmapstate");

  if (isHeatmapVisible()) {
    heatmapWrap.style.display = "";
    updateHeatmap(null);
    heatmapState.textContent = "loading…";
  } else {
    heatmapWrap.style.display = "none";
  }

  // Wait for heatmap data
  const heatmapData = await heatmapPromise;
  if (myId !== requestSequence) return;
  stopStatusPoll();

  // Update heatmap
  if (isHeatmapVisible()) {
    if (heatmapData?.t?.length) {
      updateHeatmap(heatmapData, entityNameMap());
      const allScores = (heatmapData.scores || []).flat();
      const observedCount = allScores.filter((v) => v != null).length;
      heatmapState.textContent = `ready · ${observedCount}/${allScores.length} scored`;
    } else {
      updateHeatmap(null);
      heatmapState.textContent = "unavailable";
    }
  }
}

// ── Event handlers ───────────────────────────────────────────────────────

$("#clearCache").onclick = async () => {
  if (!confirm("Clear all cached data?")) return;
  try {
    await api("/api/clear-cache");
    location.reload();
  } catch (err) {
    $("#stats").textContent = `Failed: ${err.message}`;
  }
};

function applyRange(start, end) {
  if (start == null || end == null || end <= start) return;

  view = { t0: start, t1: end };
  clearZoom();
  if (navTimer) { clearTimeout(navTimer); navTimer = null; }
  navBurst = false;
  destroyPlot();
  document.getElementById("chart").innerHTML = "";
  $("#stats").textContent = "Loading…";
  fetchData();
}

$("#applyRange").onclick = () =>
  applyRange(parseLocalISO($("#dt0").value), parseLocalISO($("#dt1").value));

function seekTo(time) {
  const span = view.t1 - view.t0 || 3600;
  const start = dataExtent
    ? Math.max(dataExtent.t0, time - span / 2)
    : time - span / 2;
  const end = Math.min(dataExtent?.t1 || time + span / 2, start + span);
  applyRange(start, end);
}

function entityNameMap() {
  return Object.fromEntries(
    entities.map((e) => [e.entity_id, e.label || e.name])
  );
}

// Filter input
$("#filter").addEventListener("input", (e) => renderEntityList(e.target.value));

// Dataset selector
$("#datasetSelect")?.addEventListener("change", (e) => {
  if (!e.target.value) return;
  destroyPlot();
  document.getElementById("chart").innerHTML = "";
  selected = [];
  buildLegend([]);
  loadDataset(e.target.value);
});

// Register config
$("#registerConfig")?.addEventListener("click", async () => {
  const raw = $("#configJson")?.value;
  if (!raw?.trim()) return;

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (err) {
    $("#stats").textContent = `Invalid JSON: ${err.message}`;
    return;
  }

  $("#stats").textContent = "Registering…";
  try {
    await apiPost("/api/configs", { config: payload });
    $("#configJson").value = "";
    $("#stats").textContent = "Registered";
    await loadDataSources();
  } catch (err) {
    $("#stats").textContent = `Failed: ${err.message}`;
  }
});

// Toggle controls
$("#showHeatmap").addEventListener("change", () => {
  if (selected.length && view.t0) fetchData();
});

// Interpolation mode change: re-render instantly from cached data (no backend call)
function rerenderInterpolation() {
  console.log("[tsviz] interpolation changed:", getInterpolationMode(), "results:", lastResults?.length);
  if (!lastResults?.length || !selected.length || view.t0 === null) return;

  const interpMode = getInterpolationMode();
  const { data, bandByEntity, entityMeta, stats } =
    buildChartData(lastResults, interpMode);

  if (stats.hasData) {
    // Force full rebuild to guarantee visual update
    destroyPlot();
    chartUpdate(data, selected, navigateTo,
      $("#perAxis").checked, bandByEntity, entityMeta, interpMode);
    if (isHistogramVisible()) {
      updateHistograms(selected, lastRenderData, getHistogramBinCount());
    }
  }
}

$("#interpMode").addEventListener("change", rerenderInterpolation);

$("#showHist").addEventListener("change", () => {
  const wrap = document.getElementById("histwrap");
  if (isHistogramVisible()) {
    wrap.style.display = "";
    if (lastRenderData) updateHistograms(selected, lastRenderData, getHistogramBinCount());
  } else {
    wrap.style.display = "none";
    clearHistogramHover();
  }
});

// Histogram bin count
const savedBins = Number(localStorage.getItem("histBins"));
if (savedBins >= 4 && savedBins <= 100) {
  $("#histBins").value = String(Math.round(savedBins));
}
$("#histBins").addEventListener("input", () => {
  const count = getHistogramBinCount();
  localStorage.setItem("histBins", String(count));
  if (isHistogramVisible() && lastRenderData) {
    updateHistograms(selected, lastRenderData, count);
  }
});

// Per-entity Y axes
$("#perAxis").addEventListener("change", () => {
  if (lastRenderData?.data && selected.length) {
    destroyPlot();
    chartUpdate(
      lastRenderData.data, selected, navigateTo,
      $("#perAxis").checked, lastRenderData.bandByEntity, lastRenderData.entityMeta,
      lastRenderData.interpMode
    );
  }
});

// Window resize
window.addEventListener("resize", () => {
  if (selected.length && view.t0) fetchData();
});

// ── Heatmap → chart tooltip info ─────────────────────────────────────────

function graphInfoAt(time) {
  const got = getValuesAtTime(time);
  if (!got) return "";

  let html = '<div style="border-top:1px solid #e5e5e5;margin-top:4px;padding-top:3px">';
  got.selected.forEach((entity, i) => {
    const value = got.values[i];
    const color = COLORS[i % COLORS.length];
    const displayValue = value == null ? "-" : Math.round(value * 1e6) / 1e6;
    html += `
      <div class="tt-row">
        <span class="tt-swatch" style="background:${color}"></span>
        <span class="tt-name">${entity.label || entity.name}</span>
        <span class="tt-val">${displayValue}</span>
      </div>`;
  });
  return html + "</div>";
}

// ── Initialize ───────────────────────────────────────────────────────────

initHeatmap(document.getElementById("heatmap"), {
  onSeek: seekTo,
  onHover: (time) => {
    setChartCursor(time);
    if (isHistogramVisible()) setHistogramHover(time);
  },
});

setHoverSync((time) => {
  highlightColumn(time);
  if (isHistogramVisible()) setHistogramHover(time);
});

setChartDrawSync(redrawHeatmap);

setTooltipEnricher((entityIndex, value) =>
  isHistogramVisible() ? binInfoLine(entityIndex, value) : ""
);

setHeatmapTooltipExtra(graphInfoAt);
watchChartResize();
loadDataSources();
