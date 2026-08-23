/**
 * heatmap.js — Canvas-based cross-entity temporal z-score heatmap.
 *
 * Color scale:
 *   z ≈ -3 (below normal) → blue
 *   z ≈  0 (normal)       → green
 *   z ≈ +3 (anomalous)    → red
 *   null                  → gray (missing)
 *
 * The heatmap is pixel-aligned with the main chart through the shared
 * x-scale. Every chart redraw calls redrawHeatmap() to keep them in sync.
 *
 * Features:
 *  - Entity names on Y axis, time labels on X axis
 *  - Hover crosshair + tooltip (entity, time, z-score)
 *  - Click to jump the main chart to that time
 *  - Drag handle to resize height (persisted in localStorage)
 */
import { plot as mainPlot, getPlotGeometry, xForTime } from "./chart.js";

// ── State ────────────────────────────────────────────────────────────────

let canvas = null;
let data = null;       // current heatmap data
let nameMap = {};      // entity ID → display label
let onSeek = null;     // click callback: (unixTime) => void
let onHover = null;    // hover callback: (unixTime | null) => void
let tooltipExtra = null; // extra tooltip HTML: (unixTime) => string
let hover = { col: -1, row: -1 };
let rafId = null;      // requestAnimationFrame handle
let lastPaintedKey = null; // geometry key of last paint (skip no-op repaints)

// ── Public API ───────────────────────────────────────────────────────────

/**
 * Initialize the heatmap. Call once on page load.
 * @param {HTMLCanvasElement} canvasEl
 * @param {Object} opts - { onSeek, onHover }
 */
export function initHeatmap(canvasEl, opts = {}) {
  canvas = canvasEl;
  onSeek = opts.onSeek || null;
  onHover = opts.onHover || null;

  canvas.addEventListener("mousemove", handleMouseMove);
  canvas.addEventListener("mouseleave", () => {
    hover = { col: -1, row: -1 };
    hideTooltip();
    onHover?.(null);
    render();
  });
  canvas.addEventListener("click", (e) => {
    if (!data || !onSeek) return;
    const col = columnAtX(e.offsetX);
    if (col >= 0) onSeek(data.t[col]);
  });

  setupDragResizer();
}

/** Register extra tooltip HTML callback. */
export function setHeatmapTooltipExtra(fn) {
  tooltipExtra = fn;
}

/**
 * Update the heatmap with new data.
 * @param {Object|null} newData - { t: float[], entity_ids: string[], scores: (float|null)[][] }
 * @param {Object} entityNames - { entityId: displayLabel }
 */
export function updateHeatmap(newData, entityNames = {}) {
  if (!canvas || !newData?.t?.length || !newData?.entity_ids?.length) {
    data = null;
    hover = { col: -1, row: -1 };
    canvas?.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    clearAxis("heatmap-yaxis");
    clearAxis("heatmap-xaxis");
    return;
  }

  nameMap = entityNames;
  data = newData;
  hover = { col: -1, row: -1 };
  sizeToRowCount(data.entity_ids.length);

  requestAnimationFrame(() => {
    if (data === newData) render();
  });
}

/** Re-render with current chart geometry (called from chart's draw hook). */
export function redrawHeatmap() {
  if (!data || rafId) return;

  const key = geometryKey();
  if (lastPaintedKey
    && key.cssWidth === lastPaintedKey.cssWidth
    && key.gutter === lastPaintedKey.gutter
    && key.plotWidth === lastPaintedKey.plotWidth
    && key.xMin === lastPaintedKey.xMin
    && key.xMax === lastPaintedKey.xMax) {
    return; // nothing changed, skip repaint
  }

  rafId = requestAnimationFrame(() => {
    rafId = null;
    if (data) render();
  });
}

/** Highlight the column at the given timestamp (or clear it). */
export function highlightColumn(time) {
  if (!data) return;

  if (time == null) {
    hover = { col: -1, row: -1 };
    render();
    return;
  }

  hover = { col: findNearestColumn(time), row: -1 };
  render();
}

// ── Geometry helpers ─────────────────────────────────────────────────────

function geometryKey() {
  const geo = getPlotGeometry();
  let xMin = null, xMax = null;
  if (mainPlot?.scales?.x) {
    xMin = mainPlot.scales.x.min ?? null;
    xMax = mainPlot.scales.x.max ?? null;
  }
  return { cssWidth: geo.cssWidth, gutter: geo.gutter, plotWidth: geo.plotWidth, xMin, xMax };
}

function findNearestColumn(time) {
  const arr = data.t;
  let lo = 0, hi = arr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < time) lo = mid; else hi = mid;
  }
  return Math.abs(arr[lo] - time) <= Math.abs(arr[hi] - time) ? lo : hi;
}

function sizeToRowCount(nRows) {
  const parent = canvas.parentElement;
  if (!parent) return;

  const saved = Number(localStorage.getItem("hmHeight"));
  if (saved >= 48) {
    parent.style.height = Math.min(saved, 520) + "px";
  } else {
    const height = Math.min(Math.max(nRows * 13, 56), 176);
    parent.style.height = height + "px";
  }
}

function dataRange() {
  if (!data?.t?.length) return null;
  return { t0: data.t[0], t1: data.t[data.t.length - 1] };
}

/** Get the pixel span [x0, x1) for column i, aligned to the chart. */
function columnSpan(index, geometry) {
  const n = data.t.length;
  let lo, hi;

  if (n > 1) {
    const dt = (data.t[n - 1] - data.t[0]) / (n - 1);
    lo = data.t[index] - dt / 2;
    hi = data.t[index] + dt / 2;
  } else {
    lo = hi = data.t[index];
  }

  const range = dataRange();
  return {
    x0: geometry.gutter + xForTime(lo, range),
    x1: geometry.gutter + xForTime(hi, range),
  };
}

// ── Render ───────────────────────────────────────────────────────────────

function render() {
  const nCols = data.t.length;
  const nRows = data.entity_ids.length;
  const ctx = canvas.getContext("2d");
  const { cssWidth, gutter, plotWidth } = getPlotGeometry();

  // Match canvas size to CSS layout
  if (cssWidth > 0 && canvas.style.width !== cssWidth + "px") {
    canvas.style.width = cssWidth + "px";
    document.getElementById("heatmap-xaxis")?.style.setProperty("width", cssWidth + "px");
  }

  const cssHeight = canvas.offsetHeight || 160;
  if (canvas.width !== cssWidth) canvas.width = cssWidth;
  if (canvas.height !== cssHeight) canvas.height = cssHeight;

  const cellHeight = Math.max(1, cssHeight / nRows);
  const plotRight = gutter + plotWidth;

  ctx.clearRect(0, 0, cssWidth, cssHeight);

  // Pre-compute column pixel spans
  const spans = Array.from({ length: nCols }, (_, c) =>
    columnSpan(c, { gutter, plotWidth })
  );

  // Draw cells
  for (let row = 0; row < nRows; row++) {
    for (let col = 0; col < nCols; col++) {
      const span = spans[col];
      const x0 = Math.max(gutter, Math.round(span.x0));
      const x1 = Math.min(plotRight, Math.round(span.x1));
      if (x1 <= x0) continue;

      const score = data.scores[row]?.[col];
      ctx.fillStyle = score == null ? "rgb(125,125,125)" : scoreToColor(score);
      ctx.fillRect(x0, Math.round(row * cellHeight), x1 - x0, Math.ceil(cellHeight));
    }
  }

  // Draw hover highlight
  if (hover.col >= 0 && hover.col < nCols) {
    const span = spans[hover.col];
    const x0 = Math.max(gutter, Math.round(span.x0));
    const x1 = Math.min(plotRight, Math.round(span.x1));
    if (x1 > x0) {
      ctx.fillStyle = "rgba(255,255,255,0.18)";
      ctx.fillRect(x0, 0, x1 - x0, cssHeight);
    }
  }

  renderYAxis(data.entity_ids, nRows, cssHeight, cellHeight);
  renderXAxis(data.t, nCols, cssWidth, spans);
  lastPaintedKey = geometryKey();
}

// ── Color scale ──────────────────────────────────────────────────────────

function scoreToColor(score) {
  const stops = [
    [0.00, [49, 54, 149]],    // blue (negative)
    [0.25, [0, 166, 81]],     // green (mild negative)
    [0.50, [166, 217, 106]],  // light green (normal)
    [0.75, [255, 235, 59]],   // yellow (mild positive)
    [1.00, [215, 48, 39]],    // red (positive)
  ];

  const s = Math.max(0, Math.min(1, score));

  for (let i = 1; i < stops.length; i++) {
    if (s <= stops[i][0]) {
      const [prevPos, prevColor] = stops[i - 1];
      const [nextPos, nextColor] = stops[i];
      const t = (s - prevPos) / (nextPos - prevPos);
      const r = Math.round(prevColor[0] + t * (nextColor[0] - prevColor[0]));
      const g = Math.round(prevColor[1] + t * (nextColor[1] - prevColor[1]));
      const b = Math.round(prevColor[2] + t * (nextColor[2] - prevColor[2]));
      return `rgb(${r},${g},${b})`;
    }
  }

  return "rgb(215,48,39)";
}

// ── Mouse interaction ────────────────────────────────────────────────────

function handleMouseMove(e) {
  if (!data) return;

  const col = columnAtX(e.offsetX);
  const cssHeight = canvas.offsetHeight || 160;
  const cellHeight = cssHeight / data.entity_ids.length;
  const row = Math.min(
    data.entity_ids.length - 1,
    Math.floor(e.offsetY / cellHeight)
  );

  if (col !== hover.col) {
    hover = { col, row };
    render();
  } else {
    hover.row = row;
  }

  showTooltip(e, col, row);
  onHover?.(col >= 0 ? data.t[col] : null);
}

function columnAtX(x) {
  if (!data?.t.length) return -1;
  const time = timeAtX(x);
  return time == null ? -1 : findNearestColumn(time);
}

function timeAtX(x) {
  if (!data) return null;

  const geometry = getPlotGeometry();

  if (mainPlot) {
    const time = mainPlot.posToVal(x - geometry.gutter, "x");
    if (isFinite(time)) return time;
  }

  const range = dataRange();
  if (range && range.t1 > range.t0 && x >= geometry.gutter) {
    return range.t0 + ((x - geometry.gutter) / geometry.plotWidth) * (range.t1 - range.t0);
  }

  return null;
}

// ── Tooltip ──────────────────────────────────────────────────────────────

function showTooltip(e, col, row) {
  const tip = document.getElementById("hm-tooltip");
  if (!tip || col < 0 || row < 0 || !data?.t?.[col]) {
    hideTooltip();
    return;
  }

  const score = data.scores?.[row]?.[col];
  const entityId = data.entity_ids[row];
  const name = nameMap[entityId] || `ch ${entityId}`;
  const timeStr = new Date(data.t[col] * 1000).toLocaleString();

  let html = `<b>${name}</b> · ${timeStr}<br>`;

  if (score == null) {
    html += '<span class="hm-missing">no sample</span>';
  } else {
    const zScore = (score * 6 - 3).toFixed(2);
    html += `z-score <b>${zScore}</b>`;
  }

  if (tooltipExtra) {
    const extra = tooltipExtra(data.t[col]);
    if (extra) html += `<div class="hm-extra">${extra}</div>`;
  }

  tip.innerHTML = html;
  tip.style.display = "block";
  tip.style.left = Math.min(innerWidth - 260, e.clientX + 14) + "px";
  tip.style.top = Math.min(innerHeight - 90, e.clientY + 14) + "px";
}

function hideTooltip() {
  const tip = document.getElementById("hm-tooltip");
  if (tip) tip.style.display = "none";
}

// ── Axis labels ──────────────────────────────────────────────────────────

function renderYAxis(entityIds, nRows, cssHeight, cellHeight) {
  const el = document.getElementById("heatmap-yaxis");
  if (!el) return;

  el.innerHTML = "";
  el.style.height = cssHeight + "px";

  // Only label every Nth row so they don't overlap
  const minLabelHeight = 12;
  const step = Math.max(1, Math.ceil(minLabelHeight / cellHeight));

  for (let r = 0; r < nRows; r += step) {
    const label = document.createElement("div");
    label.className = "hm-label";
    label.style.top = Math.round(r * cellHeight + cellHeight / 2) + "px";

    const entityId = entityIds[r];
    label.textContent = nameMap[entityId] || `ch ${entityId}`;

    el.appendChild(label);
  }
}

function renderXAxis(timeArray, nCols, cssWidth, spans) {
  const el = document.getElementById("heatmap-xaxis");
  if (!el) return;

  el.innerHTML = "";
  el.style.width = cssWidth + "px";

  const maxLabels = Math.max(2, Math.min(6, Math.floor(cssWidth / 130)));

  for (let i = 0; i < maxLabels; i++) {
    const fraction = i / (maxLabels - 1);
    const col = Math.min(nCols - 1, Math.round(fraction * (nCols - 1)));
    const span = spans[col];

    const label = document.createElement("div");
    label.className = "hm-xlabel";
    label.style.left = Math.round((span.x0 + span.x1) / 2) + "px";
    label.textContent = new Date(timeArray[col] * 1000).toLocaleString();

    el.appendChild(label);
  }
}

function clearAxis(elementId) {
  const el = document.getElementById(elementId);
  if (el) el.innerHTML = "";
}

// ── Drag resizer ─────────────────────────────────────────────────────────

function setupDragResizer() {
  const resizer = document.getElementById("heatmap-resizer");
  if (!resizer) return;

  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    resizer.classList.add("active");

    const startY = e.clientY;
    const startHeight = canvas.parentElement.getBoundingClientRect().height;

    const onMouseMove = (ev) => {
      const newHeight = Math.min(Math.max(startHeight + (startY - ev.clientY), 48), 520);
      canvas.parentElement.style.height = newHeight + "px";
      localStorage.setItem("hmHeight", String(Math.round(newHeight)));
      redrawHeatmap();
    };

    const onMouseUp = () => {
      removeEventListener("mousemove", onMouseMove);
      removeEventListener("mouseup", onMouseUp);
      resizer.classList.remove("active");
    };

    addEventListener("mousemove", onMouseMove);
    addEventListener("mouseup", onMouseUp);
  });

  // Double-click to auto-size
  resizer.addEventListener("dblclick", () => {
    localStorage.removeItem("hmHeight");
    if (data) {
      sizeToRowCount(data.entity_ids.length);
      redrawHeatmap();
    }
  });
}
