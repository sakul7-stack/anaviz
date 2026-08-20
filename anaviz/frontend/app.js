/**
 * app.js — dataset discovery, application state, canonical API calls,
 *          and coordination between visualization panels.
 */
import {
  $, COLORS, MAX_SERIES, px, toLocalISO, parseLocalISO, api, apiPost,
  seriesResponseToRenderer,
  matrixResponseToHeatmap, buildSharedTimeAxis, interpTo, alignExact,
} from './utils.js';
import {
  plot, lastRenderData,
  destroyPlot, chartUpdate, buildLegend,
  setZoom, clearZoom, watchChartResize,
  setChartCursor, setHoverSync, setChartDrawSync, setTooltipEnricher,
  getValuesAtTime,
} from './chart.js';
import {
  initHeatmap, updateHeatmap, highlightColumn, redrawHeatmap,
  setHeatmapTooltipExtra,
} from './heatmap.js';
import {
  updateHistograms, clearHistograms, setHistogramHover,
  clearHistogramHover, binInfoLine,
} from './histogram.js';

// ── State ─────────────────────────────────────────────────────────────────────
let entities      = [];
let selected      = [];
let datasetId     = null;
let measureId     = "value";
let view          = { t0: null, t1: null };
let queryRange    = { t0: null, t1: null };
let dataExtent    = null;

// ── Controls ──────────────────────────────────────────────────────────────────
const showHeatmap = () => $("#showHeatmap").checked;
const showHist    = () => $("#showHist").checked;
const histBinCount = () => Math.min(100, Math.max(4,
  Math.round(Number($("#histBins")?.value) || 24)));

// ── Boot ──────────────────────────────────────────────────────────────────────
async function loadDataset(id) {
  try {
    const datasets = await api("/api/datasets", {});
    if (!datasets?.length) throw new Error("no datasets are configured");
    datasetId = id || datasets[0].id;
    const base = `/api/datasets/${encodeURIComponent(datasetId)}`;
    const [entityPage, schema, extent] = await Promise.all([
      api(`${base}/entities`, { limit: 10000 }),
      api(`${base}/schema`, {}),
      api(`${base}/extent`, {}),
    ]);
    measureId = schema?.measures?.[0]?.id || "value";
    entities = (entityPage.items || []).map(entity => ({
      entity_id: entity.id,
      label: entity.label,
      name: entity.label,
      attributes: entity.attributes || {},
    }));
    dataExtent = { t0: extent.start, t1: extent.end };
    document.title = `${schema.label} · Time-series Explorer`;
    const heading = document.querySelector('header h1');
    if (heading) heading.textContent = schema.label.toUpperCase();
    if (!entities.length) {
      $("#entitylist").innerHTML =
        '<div style="color:var(--dim);padding:8px">no entities available</div>';
      return;
    }
    renderEntityList("");
    const t0 = extent.end - 86400;
    const t1 = extent.end;
    $("#dt0").value = toLocalISO(new Date(t0 * 1000));
    $("#dt1").value = toLocalISO(new Date(t1 * 1000));
  } catch (err) {
    console.error('dataset discovery failed', err);
    $("#entitylist").innerHTML =
      `<div style="color:#b00;padding:8px">failed to load dataset: ${err.message}</div>`;
  }
}

// Populate the Data Sources selector + registered-config list, then load the
// currently selected dataset (or the first one).
async function loadDataSources() {
  try {
    const [datasets, configs] = await Promise.all([
      api("/api/datasets", {}),
      api("/api/configs", {}),
    ]);
    const select = $("#datasetSelect");
    if (!select) return;
    const previous = select.value;
    select.innerHTML = "";
    (datasets || []).forEach(ds => {
      const opt = document.createElement("option");
      opt.value = ds.id;
      opt.textContent = ds.label;
      select.appendChild(opt);
    });
    if (previous && datasets.some(ds => ds.id === previous)) {
      select.value = previous;
    }
    renderConfigList(configs || []);
    if (datasets?.length) {
      await loadDataset(select.value || datasets[0].id);
    } else {
      // No preloaded datasets — the user must register their own source.
      showEmptyState();
    }
  } catch (err) {
    console.error('data sources failed to load', err);
  }
}

// First-run / zero-dataset state: guide the user to the Data sources panel.
function showEmptyState() {
  const panel = $("#dsPanel");
  if (panel) panel.open = true;
  $("#entitylist").innerHTML =
    '<div class="empty-state">' +
      '<div class="empty-title">No data source registered</div>' +
      '<div class="empty-sub">Open the <b>Data sources</b> panel above, paste ' +
      'your API URL + config.json, and click <b>Register</b>.</div>' +
    '</div>';
  const legend = $("#legend");
  if (legend) legend.textContent =
    'Register a data source to start exploring';
  const stats = $("#stats");
  if (stats) stats.textContent = 'no data source';
}

function renderConfigList(configs) {
  const list = $("#configList");
  if (!list) return;
  list.innerHTML = "";
  (configs || []).forEach(cfg => {
    const row = document.createElement("div");
    row.className = "config-row";
    const info = document.createElement("span");
    info.className = "config-info";
    info.textContent = cfg.label;
    info.title = `${cfg.id} · fingerprint ${cfg.fingerprint}`;
    const del = document.createElement("button");
    del.className = "config-del";
    del.textContent = "✕";
    del.title = "Remove this dataset and its cache";
    del.onclick = async () => {
      if (!confirm(`Remove dataset "${cfg.label}" and drop its cache?`)) return;
      try {
        await api(`/api/configs/${encodeURIComponent(cfg.id)}`, {}, null, "DELETE");
        await loadDataSources();
      } catch (err) {
        $("#stats").textContent = `Remove failed: ${err.message}`;
      }
    };
    row.appendChild(info);
    row.appendChild(del);
    list.appendChild(row);
  });
}

function renderEntityList(filter) {
  const list = $("#entitylist");
  list.innerHTML = "";
  entities
    .filter(c => c.name.toLowerCase().includes(filter.toLowerCase()))
    .forEach(c => {
      const label = document.createElement("label");
      const cb    = document.createElement("input");
      cb.type    = "checkbox";
      cb.checked = selected.some(s => s.entity_id === c.entity_id);
      cb.onchange = () => toggleEntity(c);
      const swatch    = document.createElement("span");
      swatch.className = "legend-swatch";
      const idx = selected.findIndex(s => s.entity_id === c.entity_id);
      swatch.style.background = idx >= 0 ? COLORS[idx % COLORS.length] : "transparent";
      label.appendChild(cb);
      label.appendChild(swatch);
      label.appendChild(document.createTextNode(c.label || c.name));
      label.title = c.name;
      list.appendChild(label);
    });
}

async function toggleEntity(c) {
  const i = selected.findIndex(s => s.entity_id === c.entity_id);
  if (i >= 0) {
    selected.splice(i, 1);
  } else if (selected.length < MAX_SERIES) {
    selected.push(c);
  } else {
    $("#stats").textContent = `Select up to ${MAX_SERIES} entities`;
  }
  renderEntityList($("#filter").value || "");
  buildLegend(selected);
}

// ── Navigation ────────────────────────────────────────────────────────────────
let navTimer   = null;
let navBurst   = false;   // a scroll/zoom burst is in progress
let abortController = null;
let requestSeq = 0;
let statusTimer = null;

function stopStatusPoll() {
  if (statusTimer) {
    clearInterval(statusTimer);
    statusTimer = null;
  }
}

function startStatusPoll() {
  stopStatusPoll();
  statusTimer = setInterval(async () => {
    try {
      const st = await api("/api/status", {});
      const r = st.refreshing || [];
      if (r.length) {
        $("#stats").innerHTML =
          `<span class="stat-block refresh">⟳ refreshing ${r.join(", ")}</span>`;
      }
    } catch { /* ignore transient poll errors */ }
  }, 800);
}

function navTo(t0, t1) {
  // Free navigation within the FULL dataset extent. The allowed window is the
  // dataset extent — NOT the last "Go" range — so zooming or panning into a
  // new region never snaps back to the initial query range. Falls back to the
  // requested range / current view only when the extent is unknown.
  let lo = dataExtent?.t0 ?? Math.min(t0, view.t0 ?? t0);
  let hi = dataExtent?.t1 ?? Math.max(t1, view.t1 ?? t1);
  if (hi <= lo) { lo = t0; hi = t1; }

  const maxSpan = hi - lo;
  let span = Math.min(Math.max(t1 - t0, 1), maxSpan);
  const c  = (t0 + t1) / 2;
  t0 = c - span / 2; t1 = c + span / 2;
  if (t0 < lo) { t0 = lo; t1 = lo + span; }
  if (t1 > hi) { t1 = hi; t0 = hi - span; }
  if (view.t0 === t0 && view.t1 === t1) return;

  view = { t0, t1 };
  setZoom(t0, t1);
  if (plot) plot.setScale("x", { min: t0, max: t1 });
  $("#dt0").value = toLocalISO(new Date(t0 * 1000));
  $("#dt1").value = toLocalISO(new Date(t1 * 1000));

  // Fetch on the leading edge (first event of a scroll/zoom burst) so the
  // chart starts filling right away instead of showing an empty stretched
  // view; then settle on the trailing edge with the final range.
  if (!navBurst) {
    navBurst = true;
    fetchData();
  }
  if (navTimer) clearTimeout(navTimer);
  navTimer = setTimeout(() => {
    navTimer = null;
    navBurst = false;
    fetchData();
  }, 400);
}

// ── Main fetch ────────────────────────────────────────────────────────────────
async function fetchData() {
  if (!selected.length || view.t0 === null) return;

  $("#stats").textContent = 'Loading…';
  if (abortController) abortController.abort();
  abortController = new AbortController();
  const signal = abortController.signal;
  const myId   = ++requestSeq;
  startStatusPoll();

  const params = {
    px: px(), k: 2,
    t0: view.t0, t1: view.t1,
    algo: $("#algo").value,
  };

  // ── Series + overlay fetches ────────────────────────────────────────────
  // The chart renders as soon as its own request completes. The heatmap
  // overlay resolves independently below.
  const seriesPromise = apiPost("/api/query", {
    dataset_id: datasetId,
    entity_ids: selected.map(entity => String(entity.entity_id)),
    measure_ids: [measureId],
    range: { start: view.t0, end: view.t1 },
    resolution: {
      strategy: "auto",
      pixel_width: params.px,
      points_per_pixel: params.k,
    },
    downsampling: params.algo,
    filters: {},
  }, signal)
    .then(seriesResponseToRenderer)
    .catch(() => []);

  const heatmapPromise = (showHeatmap() && selected.length && !navBurst)
    ? seriesPromise.then(() => apiPost("/api/matrix", {
        dataset_id: datasetId,
        entity_ids: selected.map(entity => String(entity.entity_id)),
        measure_id: measureId,
        range: { start: view.t0, end: view.t1 },
        transform: "temporal_rolling_zscore",
        pixel_width: Math.min(2000, Math.max(200,
          document.getElementById('heatmap').offsetWidth || 200)),
      }, signal).then(matrixResponseToHeatmap))
      .catch(() => null)
    : Promise.resolve(null);

  const results = await seriesPromise;
  if (myId !== requestSeq) return;
  stopStatusPoll();

  // ── Build chart data ──────────────────────────────────────────────────────
  const allT = buildSharedTimeAxis(results);

  let scanned = 0;
  let rawTotal = 0;
  let sent = 0;
  let gapTotal = 0;
  let dbMs = 0;
  let archMs = 0;
  let dsMs = 0;
  let resName = "raw";
  let respAlgo = "M4";
  let hasData = false;
  const algoUI = $("#algo").value;
  const data   = [allT];
  const bandByEntity = [];
  const entityMeta = [];

  (Array.isArray(results) ? results : []).forEach(r => {
    if (r && !r.error && r.series.t.length) {
      const s    = r.series;
      const gaps = r.gap_intervals || [];
      gapTotal += gaps.length;
      const rawMode = algoUI === 'RAW';
      const band = (r.algo === 'LTTB' || r.algo === 'MINMAXLTTB') && s.min !== undefined;
      const row  = [interpTo(allT, s.t, s.avg, rawMode, gaps)];
      if (band) {
        row.push(interpTo(allT, s.min_t || s.t, s.min, rawMode, gaps));
        row.push(interpTo(allT, s.max_t || s.t, s.max, rawMode, gaps));
      }
      data.push(...row);
      bandByEntity.push(band);
      entityMeta.push({
        observed: alignExact(allT, s.t, s.avg),
        quality: alignExact(allT, s.t, s.quality_flag || []),
        sampleCount: alignExact(allT, s.t, s.sample_count || []),
        gaps,
        expectedStep: r.expected_step_seconds,
        qualitySummary: r.quality_summary || {},
        dataFidelity: r.data_fidelity || {},
      });
      scanned  += r.rows_scanned;
      rawTotal += r.rows_raw;
      sent     += r.rows_returned;
      dbMs     = Math.max(dbMs,   r.query_ms);
      archMs   = Math.max(archMs, r.archive_ms || 0);
      dsMs     = Math.max(dsMs,   r.ds_ms || 0);
      resName  = r.resolution;
      respAlgo = r.algo || "M4";
      hasData  = true;
    } else {
      data.push(new Array(allT.length).fill(null));
      bandByEntity.push(false);
      entityMeta.push({ observed: new Array(allT.length).fill(null), gaps: [] });
    }
  });

  // ── Render chart ──────────────────────────────────────────────────────────
  if (hasData) {
    chartUpdate(
      data, selected, navTo,
      $("#perAxis").checked, bandByEntity, entityMeta
    );
    buildLegend(selected);
  }

  // The primary evidence view is complete now. Do not leave the global
  // status bar or histograms waiting on optional overlays.
  const nFailed = selected.length - (Array.isArray(results)
    ? results.filter(r => r && !r.error).length : 0);
  const spanHours = ((view.t1 - view.t0) / 3600).toFixed(1);
  const errHtml = nFailed
    ? `<span class="stat-err"> ${nFailed} series failed</span>`
    : "";

  if (hasData) {
    const stats = [
      ['series', selected.length],
      ['range', `${spanHours}h`],
      ['tier', resName],
      ['raw', rawTotal.toLocaleString()],
      ['db', scanned.toLocaleString()],
      ['out', sent.toLocaleString()],
      ['gaps', gapTotal],
      ['db', `${dbMs}ms`],
      [respAlgo, `${dsMs}ms`],
    ];
    $("#stats").innerHTML = stats
      .map(([label, value]) =>
        `<span class="stat-block"><span class="stat-label">${label}</span>${value}</span>`)
      .join('') + errHtml;
  } else {
    $("#stats").textContent = "no data in selected range";
  }

  const histWrap = document.getElementById('histwrap');
  if (showHist()) {
    histWrap.style.display = '';
    if (hasData && lastRenderData) {
      updateHistograms(selected, lastRenderData, histBinCount());
    } else {
      clearHistograms();
    }
  } else {
    histWrap.style.display = 'none';
  }

  // ── Heatmap overlay ─────────────────────────────────────────────────────
  const hmWrap = document.getElementById('heatmapwrap');
  const hmState = document.getElementById('heatmapstate');
  if (showHeatmap()) {
    hmWrap.style.display = '';
    updateHeatmap(null);
    if (hmState) hmState.textContent = 'loading…';
  } else {
    hmWrap.style.display = 'none';
  }

  const heatmapData = await heatmapPromise;
  if (myId !== requestSeq) return;
  stopStatusPoll();

  if (showHeatmap()) {
    if (heatmapData?.t?.length) {
      updateHeatmap(heatmapData, hmNameMap());
      if (hmState) {
        const flat = (heatmapData.scores || []).flat();
        const observed = flat.filter(v => v !== null && v !== undefined).length;
        hmState.textContent = `ready · ${observed}/${flat.length} scored cells`;
      }
    } else {
      updateHeatmap(null);
      if (hmState) hmState.textContent = 'unavailable';
    }
  }
}

// ── Event handlers ────────────────────────────────────────────────────────────
$("#clearCache").onclick = async () => {
  if (!confirm("Clear all cached data and reload?")) return;
  try {
    await api("/api/clear-cache", {});
    location.reload();
  } catch (err) {
    $("#stats").textContent = `Clear cache failed: ${err.message}`;
  }
};

function applyRange(t0, t1) {
  if (t0 === null || t1 === null || t1 <= t0) return;
  view       = { t0, t1 };
  queryRange = { t0, t1 };
  clearZoom();
  if (navTimer) {
    clearTimeout(navTimer);
    navTimer = null;
  }
  navBurst = false;
  destroyPlot();
  document.getElementById('chart').innerHTML = '';
  $("#stats").textContent = 'Loading…';
  fetchData();
}

$("#applyRange").onclick = () =>
  applyRange(parseLocalISO($("#dt0").value), parseLocalISO($("#dt1").value));

// Jump the main chart to the given timestamp (heatmap click) — recentres on
// the clicked time while keeping the current span, like pressing Go.
function seekTo(t) {
  const span = view.t1 - view.t0 || 3600;
  const t0 = (dataExtent ? Math.max(dataExtent.t0, t - span / 2) : t - span / 2);
  const t1 = Math.min((dataExtent ? dataExtent.t1 : t + span / 2), t0 + span);
  applyRange(t0, t1);
}

// Canonical entity ID → display label, for heatmap axis labels
function hmNameMap() {
  return Object.fromEntries(
    entities.map(c => [c.entity_id, c.label || c.name])
  );
}

$("#filter").addEventListener("input", e => renderEntityList(e.target.value));

// Data Sources panel: switch active dataset / register a new config.json
$("#datasetSelect")?.addEventListener("change", e => {
  if (!e.target.value) return;
  destroyPlot();
  document.getElementById('chart').innerHTML = '';
  selected = [];
  buildLegend([]);
  loadDataset(e.target.value);
});

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
  $("#stats").textContent = 'Registering data source…';
  try {
    await apiPost("/api/configs", { config: payload });
    $("#configJson").value = "";
    $("#stats").textContent = 'Data source registered';
    await loadDataSources();
  } catch (err) {
    $("#stats").textContent = `Register failed: ${err.message}`;
  }
});

// Re-render with current data when toggles change
$("#showHeatmap").addEventListener("change", () => {
  if (selected.length && view.t0 !== null) fetchData();
});

// Histograms rebuild from the last chart data — instant, no refetch needed.
$("#showHist").addEventListener("change", () => {
  const wrap = document.getElementById('histwrap');
  if (showHist()) {
    wrap.style.display = '';
    if (lastRenderData) updateHistograms(selected, lastRenderData, histBinCount());
  } else {
    wrap.style.display = 'none';
    clearHistogramHover();
  }
});

const savedHistBins = Number(localStorage.getItem('histBins'));
if (savedHistBins >= 4 && savedHistBins <= 100) {
  $("#histBins").value = String(Math.round(savedHistBins));
}
$("#histBins").addEventListener("input", () => {
  const bins = histBinCount();
  localStorage.setItem('histBins', String(bins));
  if (showHist() && lastRenderData) {
    updateHistograms(selected, lastRenderData, bins);
  }
});

$("#perAxis").addEventListener("change", () => {
  if (lastRenderData?.data && selected.length) {
    destroyPlot();
    chartUpdate(
      lastRenderData.data, selected, navTo,
      $("#perAxis").checked, lastRenderData.bandByEntity,
      lastRenderData.entityMeta
    );
  }
});

window.addEventListener("resize", () => {
  if (selected.length && view.t0) fetchData();
});

// Extra HTML for the heatmap tooltip: the graph's values at that time.
function graphInfoAt(t) {
  const got = getValuesAtTime(t);
  if (!got) return '';
  let html = '<div style="border-top:1px solid #e5e5e5;margin-top:4px;padding-top:3px">';
  got.selected.forEach((c, i) => {
    const v = got.values[i];
    html += `<div class="tt-row">
      <span class="tt-swatch" style="background:${COLORS[i % COLORS.length]}"></span>
      <span class="tt-name">${c.label || c.name}</span>
      <span class="tt-val">${v === null || v === undefined ? '-' : Math.round(v * 1e6) / 1e6}</span>
    </div>`;
  });
  return html + '</div>';
}

// ── Init heatmap canvas + chart resize watching ──────────────────────────────
initHeatmap(document.getElementById('heatmap'), {
  onSeek:  seekTo,
  onHover: t => { setChartCursor(t); if (showHist()) setHistogramHover(t); },
});
setHoverSync(t => { highlightColumn(t); if (showHist()) setHistogramHover(t); });
setChartDrawSync(redrawHeatmap);            // every chart redraw re-syncs the heatmap
setTooltipEnricher((i, v) => showHist() ? binInfoLine(i, v) : '');
setHeatmapTooltipExtra(graphInfoAt);        // heatmap tooltip shows the graph's values
watchChartResize();

// ── Boot ──────────────────────────────────────────────────────────────────────
loadDataSources();
