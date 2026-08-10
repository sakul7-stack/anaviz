/**
 * app.js — state management, API calls, channel selection,
 *           SPOT score fetching.
 */
import { $, COLORS, px, toLocalISO, parseLocalISO, api, interpTo } from './utils.js';
import {
  plot, lastRenderData,
  destroyPlot, chartUpdate, buildLegend,
  renderScorePanel, setZoom, clearZoom, watchChartResize,
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
let channels      = [];
let selected      = [];
let view          = { t0: null, t1: null };
let queryRange    = { t0: null, t1: null };
let dataExtent    = null;
let archiveExtent = null;

// ── Controls ──────────────────────────────────────────────────────────────────
const showScore   = () => $("#showScore").checked;
const showHeatmap = () => $("#showHeatmap").checked;
const showHist    = () => $("#showHist").checked;

// ── Boot ──────────────────────────────────────────────────────────────────────
async function loadChannels() {
  try {
    [channels, archiveExtent] = await Promise.all([
      api("/api/channels", {}),
      api("/api/extent",   {}),
    ]);
    dataExtent = { t0: archiveExtent.t_min, t1: archiveExtent.t_max };
    if (!channels?.length) {
      $("#chanlist").innerHTML =
        '<div style="color:var(--dim);padding:8px">no channels available<br>' +
        'Run: python -m server ingest --data-dir data/hlt</div>';
      return;
    }
    renderChanList("");
    const t0 = archiveExtent.t_max - 86400;
    const t1 = archiveExtent.t_max;
    $("#dt0").value = toLocalISO(new Date(t0 * 1000));
    $("#dt1").value = toLocalISO(new Date(t1 * 1000));
  } catch (err) {
    console.error('loadChannels failed', err);
    $("#chanlist").innerHTML =
      `<div style="color:#b00;padding:8px">failed to load channels: ${err.message}</div>`;
  }
}

function renderChanList(filter) {
  const list = $("#chanlist");
  list.innerHTML = "";
  channels
    .filter(c => c.name.toLowerCase().includes(filter.toLowerCase()))
    .forEach(c => {
      const label = document.createElement("label");
      const cb    = document.createElement("input");
      cb.type    = "checkbox";
      cb.checked = selected.some(s => s.element_id === c.element_id);
      cb.onchange = () => toggleChannel(c);
      const swatch    = document.createElement("span");
      swatch.className = "legend-swatch";
      const idx = selected.findIndex(s => s.element_id === c.element_id);
      swatch.style.background = idx >= 0 ? COLORS[idx % COLORS.length] : "transparent";
      label.appendChild(cb);
      label.appendChild(swatch);
      label.appendChild(document.createTextNode(c.name.replace(/^ATLAS_/, "")));
      label.title = c.name;
      list.appendChild(label);
    });
}

async function toggleChannel(c) {
  const i = selected.findIndex(s => s.element_id === c.element_id);
  if (i >= 0) selected.splice(i, 1); else selected.push(c);
  renderChanList($("#filter").value || "");
  buildLegend(selected);
}

// ── Navigation ────────────────────────────────────────────────────────────────
let navTimer   = null;
let navBurst   = false;   // a scroll/zoom burst is in progress
let abortController = null;
let requestSeq = 0;
let statusTimer = null;

function stopStatusPoll() {
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
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
  let lo, hi;
  if (queryRange.t0 === null) {
    lo = dataExtent?.t0 ?? t0;
    hi = dataExtent?.t1 ?? t1;
  } else {
    const margin = Math.min(queryRange.t1 - queryRange.t0, 30 * 86400);
    lo = queryRange.t0 - margin;
    hi = queryRange.t1 + margin;
    if (dataExtent) { lo = Math.max(lo, dataExtent.t0); hi = Math.min(hi, dataExtent.t1); }
    if (hi <= lo)   { lo = dataExtent.t0; hi = dataExtent.t1; }
  }
  const maxSpan = dataExtent ? dataExtent.t1 - dataExtent.t0 : (hi - lo);
  let span = Math.min(Math.max(t1 - t0, 1), maxSpan);
  if (span > hi - lo) { lo = dataExtent?.t0 ?? lo; hi = dataExtent?.t1 ?? hi; }
  const c  = (t0 + t1) / 2;
  t0 = c - span / 2; t1 = c + span / 2;
  if (t0 < lo) { t0 = lo; t1 = Math.min(t0 + span, hi); }
  if (t1 > hi) { t1 = hi; t0 = Math.max(t1 - span, lo); }
  if (view.t0 === t0 && view.t1 === t1) return;

  view = { t0, t1 };
  setZoom(t0, t1);
  if (plot) plot.setScale("x", { min: t0, max: t1 });
  $("#dt0").value = toLocalISO(new Date(t0 * 1000));
  $("#dt1").value = toLocalISO(new Date(t1 * 1000));

  // Fetch on the leading edge (first event of a scroll/zoom burst) so the
  // chart starts filling right away instead of showing an empty stretched
  // view; then settle on the trailing edge with the final range.
  if (!navBurst) { navBurst = true; fetchData(); }
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

  // ── Series + overlay fetches (parallel) ──────────────────────────────────
  const seriesPromise = api("/api/series",
    { ...params, element_id: selected.map(c => c.element_id) }, signal)
    .catch(() => []);

  // For SPOT score: only for the FIRST selected channel (score panel is
  // per-channel; the user can select which channel they care about)
  const scorePromise = (showScore() && selected.length)
    ? api("/api/anomaly-score",
        { element_id: selected[0].element_id, t0: view.t0, t1: view.t1 }, signal)
        .catch(() => null)
    : Promise.resolve(null);

  // Heatmap is only fetched on the settling request (not the aborted
  // leading-edge fetches of a scroll/zoom burst): aborting a heatmap request
  // mid-flight left it showing the OLD range — the desync that needed a
  // toggle-off/on to repair. On the settle request it is never aborted.
  const heatmapPromise = (showHeatmap() && selected.length && !navBurst)
    ? api("/api/heatmap", {
        t0: view.t0, t1: view.t1,
        // Backend caps px at 2000 — on wide windows the visible canvas width
        // exceeds that and the request would 422, leaving the heatmap stale.
        px: Math.min(2000, Math.max(200,
          document.getElementById('heatmap').offsetWidth || 200)),
        channel_ids: selected.map(c => c.element_id).join(","),
      }, signal).catch(() => null)
    : Promise.resolve(null);

  const [results, scoreData, heatmapData] =
    await Promise.all([seriesPromise, scorePromise, heatmapPromise]);

  if (myId !== requestSeq) return;  // superseded by a newer request
  stopStatusPoll();

  // ── Build chart data ──────────────────────────────────────────────────────
  const allT = [...new Set(
    results.flatMap(r => (r && !r.error) ? r.series.t : [])
  )].sort((a, b) => a - b);

  let scanned=0, rawTotal=0, sent=0, dbMs=0, archMs=0, dsMs=0;
  let resName="raw", respAlgo="M4", hasData=false;
  const algoUI = $("#algo").value;
  const data   = [allT];
  const bandByChannel = [];

  (Array.isArray(results) ? results : []).forEach(r => {
    if (r && !r.error && r.series.t.length) {
      const s    = r.series;
      const band = (r.algo === 'LTTB' || r.algo === 'MINMAXLTTB') && s.min !== undefined;
      const row  = [interpTo(allT, s.t, s.avg, algoUI === 'RAW')];
      if (band) {
        row.push(interpTo(allT, s.min_t || s.t, s.min, algoUI === 'RAW'));
        row.push(interpTo(allT, s.max_t || s.t, s.max, algoUI === 'RAW'));
      }
      data.push(...row);
      bandByChannel.push(band);
      scanned  += r.rows_scanned;
      rawTotal += r.rows_raw;
      sent     += r.rows_returned;
      dbMs      = Math.max(dbMs,   r.query_ms);
      archMs    = Math.max(archMs, r.archive_ms || 0);
      dsMs      = Math.max(dsMs,   r.ds_ms || 0);
      resName   = r.resolution;
      respAlgo  = r.algo || "M4";
      hasData   = true;
    } else {
      data.push(new Array(allT.length).fill(null));
      bandByChannel.push(false);
    }
  });

  // ── Render chart ──────────────────────────────────────────────────────────
  if (hasData) {
    chartUpdate(
      data, selected, navTo,
      $("#perAxis").checked, bandByChannel
    );
    buildLegend(selected);
  }

  // ── SPOT score panel ──────────────────────────────────────────────────────
  const scoreWrap = document.getElementById('scorewrap');
  if (showScore()) {
    scoreWrap.style.display = '';
    renderScorePanel(scoreData);
  } else {
    scoreWrap.style.display = 'none';
  }

  // ── Heatmap ───────────────────────────────────────────────────────────────
  const hmWrap = document.getElementById('heatmapwrap');
  if (showHeatmap()) {
    hmWrap.style.display = '';
    if (heatmapData) updateHeatmap(heatmapData, hmNameMap());
    else if (!navBurst) console.warn("heatmap fetch failed for", new Date(view.t0), "→", new Date(view.t1));
  } else {
    hmWrap.style.display = 'none';
  }

  // ── Histograms (derive from the chart's own series — no extra fetch) ─────
  const histWrap = document.getElementById('histwrap');
  if (showHist()) {
    histWrap.style.display = '';
    if (hasData && lastRenderData) updateHistograms(selected, lastRenderData);
    else clearHistograms();
  } else {
    histWrap.style.display = 'none';
  }

  // ── Stats bar ─────────────────────────────────────────────────────────────
  const nFailed = selected.length - (Array.isArray(results) ? results.filter(r => r && !r.error) : []).length;
  const span    = ((view.t1 - view.t0) / 3600).toFixed(1);
  const err     = nFailed ? `<span class="stat-err"> ${nFailed} ch failed</span>` : "";
  $("#stats").innerHTML = hasData
    ? `<span class="stat-block"><span class="stat-label">ch</span>${selected.length}</span>`
    + `<span class="stat-block"><span class="stat-label">range</span>${span}h</span>`
    + `<span class="stat-block"><span class="stat-label">tier</span>${resName}</span>`
    + `<span class="stat-block"><span class="stat-label">raw</span>${rawTotal.toLocaleString()}</span>`
    + `<span class="stat-block"><span class="stat-label">db</span>${scanned.toLocaleString()}</span>`
    + `<span class="stat-block"><span class="stat-label">out</span>${sent.toLocaleString()}</span>`
    + `<span class="stat-block"><span class="stat-label">db</span>${dbMs}ms</span>`
    + `<span class="stat-block"><span class="stat-label">${respAlgo}</span>${dsMs}ms</span>`
    + err
    : "no data in selected range";
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
  if (navTimer) { clearTimeout(navTimer); navTimer = null; }
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

// element_id → short channel name, for heatmap axis labels
function hmNameMap() {
  const map = {};
  channels.forEach(c => { map[c.element_id] = c.name.replace(/^ATLAS_/, ''); });
  return map;
}

$("#filter").addEventListener("input", e => renderChanList(e.target.value));

// Re-render with current data when toggles change
["showScore","showHeatmap"].forEach(id => {
  $(`#${id}`).addEventListener("change", () => {
    if (selected.length && view.t0 !== null) fetchData();
  });
});

// Histograms rebuild from the last chart data — instant, no refetch needed.
$("#showHist").addEventListener("change", () => {
  const wrap = document.getElementById('histwrap');
  if (showHist()) {
    wrap.style.display = '';
    if (lastRenderData) updateHistograms(selected, lastRenderData);
  } else {
    wrap.style.display = 'none';
    clearHistogramHover();
  }
});

$("#perAxis").addEventListener("change", () => {
  if (lastRenderData?.data && selected.length) {
    destroyPlot();
    chartUpdate(
      lastRenderData.data, selected, navTo,
      $("#perAxis").checked, lastRenderData.bandByChannel
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
      <span class="tt-name">${c.name.replace(/^ATLAS_/, '')}</span>
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
loadChannels();
