/**
 * histogram.js — Per-entity value distribution (histogram) panel.
 *
 * One histogram per selected entity, rendered below the heatmap.
 * Bins are built from the SAME interpolated data the chart draws,
 * so histograms always reflect the visible range.
 *
 * Features:
 *  - Drag handle to resize individual histograms (persisted in localStorage)
 *  - Chart/heatmap hover marks the hovered value on each histogram
 *  - Chart tooltip enriched with which bin a hovered value falls into
 */
import { $, COLORS, MAX_SERIES } from "./utils.js";
import { getValuesAtTime } from "./chart.js";

// ── Constants ────────────────────────────────────────────────────────────

const DEFAULT_HEIGHT = 72;
const MIN_HEIGHT = 36;
const MAX_HEIGHT = 260;
const LABEL_HEIGHT = 16;  // bottom strip for value ticks

// ── State ────────────────────────────────────────────────────────────────

let rows = [];              // [{ entity, canvas, bins, ... }]
let lastRenderData = null;
let binCount = 24;
let hoverState = { time: null, values: null };

// ── Public API ───────────────────────────────────────────────────────────

/**
 * Rebuild or refresh histograms from the current chart data.
 */
export function updateHistograms(selected, renderData, count = 24) {
  lastRenderData = renderData;
  binCount = Math.min(100, Math.max(4, Math.round(Number(count) || 24)));

  const wrap = document.getElementById("hists");
  if (!wrap) return;

  const entities = (selected || []).slice(0, MAX_SERIES);
  const entityIds = entities.map((e) => e.entity_id).join(",");

  // Rebuild DOM if the entity list changed
  if (wrap.dataset.eids !== entityIds) {
    wrap.innerHTML = "";
    wrap.dataset.eids = entityIds;
    rows = entities.map((entity, i) => buildRow(wrap, entity, i));
  }

  // Set base indices and compute bins for each entity
  let base = 1;
  rows.forEach((row, i) => {
    row.base = base;
    base += renderData.bandByEntity?.[i] ? 3 : 1;

    const observedValues = renderData.entityMeta?.[i]?.observed;
    const dataRow = renderData.data?.[row.base];
    const values = (observedValues || dataRow || [])
      .filter((v) => v != null && isFinite(v));

    row.bins = makeBins(values, binCount);
  });

  rows.forEach((row) => drawHistogram(row));
}

/** Remove all histograms. */
export function clearHistograms() {
  const wrap = document.getElementById("hists");
  if (wrap) {
    wrap.innerHTML = "";
    wrap.dataset.eids = "";
  }
  rows = [];
  hoverState = { time: null, values: null };
}

/** Mark the hovered time on every histogram (vertical value marker). */
export function setHistogramHover(time) {
  hoverState.time = time;

  if (time == null) {
    hoverState.values = null;
    rows.forEach((row) => drawHistogram(row));
    return;
  }

  hoverState.values = lastRenderData
    ? getValuesAtTime(time)?.values || null
    : null;

  rows.forEach((row) => drawHistogram(row));
}

/** Clear hover markers + tooltip. */
export function clearHistogramHover() {
  hoverState = { time: null, values: null };
  rows.forEach((row) => { row.hoveredBin = -1; drawHistogram(row); });
  const tip = $("#hist-tooltip");
  if (tip) tip.style.display = "none";
}

/**
 * Extra tooltip line for the chart: which bin a hovered value falls into.
 */
export function binInfoLine(entityIndex, value) {
  const row = rows[entityIndex];
  if (!row?.bins || value == null || !isFinite(value)) return "";

  const { edges, counts } = row.bins;
  const binIndex = findBinIndex(edges, value);
  const pct = Math.round((counts[binIndex] / row.bins.total) * 1000) / 10;

  return `
    <div class="tt-row">
      <span class="tt-swatch" style="background:${row.color}"></span>
      <span class="tt-name">${formatValue(edges[binIndex])} – ${formatValue(edges[binIndex + 1])}</span>
      <span class="tt-val">${counts[binIndex]} pts</span>
    </div>`;
}

// ── Build a histogram row ────────────────────────────────────────────────

function buildRow(wrap, entity, index) {
  // Create DOM structure
  const rowEl = document.createElement("div");
  rowEl.className = "hist-row";

  const header = document.createElement("div");
  header.className = "hist-head";

  const swatch = document.createElement("span");
  swatch.className = "legend-swatch";
  swatch.style.background = COLORS[index % COLORS.length];

  const nameEl = document.createElement("span");
  nameEl.textContent = entity.label || entity.name;
  header.append(swatch, nameEl);

  const canvasEl = document.createElement("canvas");
  canvasEl.className = "hist-canvas";

  // Restore saved height
  const saved = Number(localStorage.getItem(`histH_${entity.entity_id}`));
  canvasEl.style.height =
    (saved >= MIN_HEIGHT ? Math.min(saved, MAX_HEIGHT) : DEFAULT_HEIGHT) + "px";

  const resizer = document.createElement("div");
  resizer.className = "hist-resizer";

  rowEl.append(header, canvasEl, resizer);
  wrap.appendChild(rowEl);

  // Create entry object
  const entry = {
    entityId: entity.entity_id,
    index,
    name: entity.label || entity.name,
    color: COLORS[index % COLORS.length],
    base: 1,
    bins: null,
    canvas: canvasEl,
    hoveredBin: -1,
  };

  // Drag to resize
  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    resizer.classList.add("active");

    const startY = e.clientY;
    const startHeight = canvasEl.getBoundingClientRect().height;

    const onMouseMove = (ev) => {
      const newHeight = Math.min(
        Math.max(startHeight + (startY - ev.clientY), MIN_HEIGHT),
        MAX_HEIGHT
      );
      canvasEl.style.height = newHeight + "px";
      localStorage.setItem(`histH_${entry.entityId}`, String(Math.round(newHeight)));
      drawHistogram(entry);
    };

    const onMouseUp = () => {
      removeEventListener("mousemove", onMouseMove);
      removeEventListener("mouseup", onMouseUp);
      resizer.classList.remove("active");
    };

    addEventListener("mousemove", onMouseMove);
    addEventListener("mouseup", onMouseUp);
  });

  // Double-click to reset height
  resizer.addEventListener("dblclick", () => {
    localStorage.removeItem(`histH_${entry.entityId}`);
    canvasEl.style.height = DEFAULT_HEIGHT + "px";
    drawHistogram(entry);
  });

  // Bin hover tooltip
  canvasEl.addEventListener("mousemove", (e) => handleBinHover(entry, e));
  canvasEl.addEventListener("mouseleave", () => {
    entry.hoveredBin = -1;
    drawHistogram(entry);
    const tip = $("#hist-tooltip");
    if (tip) tip.style.display = "none";
  });

  return entry;
}

// ── Bin computation ──────────────────────────────────────────────────────

function makeBins(values, nBins = 24) {
  const n = values.length;
  if (!n) return null;

  // Find range
  let min = Infinity, max = -Infinity;
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }

  // Handle single value
  if (max === min) { max = min + 1; min = min - 1; }

  // Build bins
  const binWidth = (max - min) / nBins;
  const counts = new Array(nBins).fill(0);
  for (const v of values) {
    let bin = Math.floor((v - min) / binWidth);
    if (bin >= nBins) bin = nBins - 1;
    counts[bin]++;
  }

  const edges = Array.from({ length: nBins + 1 }, (_, i) => min + i * binWidth);

  return { edges, counts, min, max, nBins, total: n };
}

function findBinIndex(edges, value) {
  for (let i = 0; i < edges.length - 1; i++) {
    if (value >= edges[i] && value < edges[i + 1]) return i;
  }
  // Past the last edge
  return value >= edges[edges.length - 1] ? edges.length - 2 : 0;
}

// ── Drawing ──────────────────────────────────────────────────────────────

function drawHistogram(row) {
  const cv = row.canvas;
  if (!cv) return;

  const cssWidth = cv.parentElement.clientWidth || 400;
  const cssHeight = cv.offsetHeight || DEFAULT_HEIGHT;
  if (cv.width !== cssWidth) cv.width = cssWidth;
  if (cv.height !== cssHeight) cv.height = cssHeight;

  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const bins = row.bins;
  const barHeight = cssHeight - LABEL_HEIGHT;

  // No data message
  if (!bins) {
    ctx.fillStyle = "#6c757d";
    ctx.font = "11px system-ui";
    ctx.fillText("no data", 8, barHeight / 2 + 4);
    return;
  }

  // Draw bars
  const maxCount = Math.max(1, ...bins.counts);
  const binWidthPct = 100 / bins.nBins;

  for (let i = 0; i < bins.nBins; i++) {
    const height = Math.max(1, Math.round((bins.counts[i] / maxCount) * (barHeight - 6)));
    const x = (binWidthPct * i) / 100 * cssWidth;
    const width = Math.max(1, (binWidthPct / 100) * cssWidth - 1);

    ctx.fillStyle = row.color;
    ctx.globalAlpha = 0.45 + 0.55 * (bins.counts[i] / maxCount);
    ctx.fillRect(Math.round(x), barHeight - height, Math.ceil(width), height);
  }
  ctx.globalAlpha = 1;

  // Draw value ticks at bottom
  ctx.fillStyle = "#6c757d";
  ctx.font = "10px system-ui";
  ctx.textBaseline = "top";
  ctx.fillText(formatValue(bins.min), 2, barHeight + 3);
  ctx.textAlign = "center";
  ctx.fillText(formatValue((bins.min + bins.max) / 2), cssWidth / 2, barHeight + 3);
  ctx.textAlign = "right";
  ctx.fillText(formatValue(bins.max), cssWidth - 2, barHeight + 3);
  ctx.textAlign = "left";

  // Draw hovered-value marker (from chart/heatmap hover)
  const hoveredValue = hoverState.values?.[row.index];
  if (hoveredValue != null && isFinite(hoveredValue)) {
    const xPos = ((hoveredValue - bins.min) / (bins.max - bins.min)) * cssWidth;

    ctx.strokeStyle = "#111";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(Math.round(xPos) + 0.5, 2);
    ctx.lineTo(Math.round(xPos) + 0.5, barHeight);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "#111";
    ctx.beginPath();
    ctx.arc(Math.round(xPos), barHeight, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // Draw hovered bin highlight
  if (row.hoveredBin >= 0) {
    const x = (binWidthPct * row.hoveredBin) / 100 * cssWidth;
    const w = Math.max(1, (binWidthPct / 100) * cssWidth - 1);
    ctx.strokeStyle = "#111";
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.round(x) + 0.5, 1, Math.ceil(w) - 1, barHeight - 2);
  }
}

// ── Bin hover ────────────────────────────────────────────────────────────

function handleBinHover(row, e) {
  const bins = row.bins;
  if (!bins) return;

  const rect = row.canvas.getBoundingClientRect();
  const fraction = (e.clientX - rect.left) / rect.width;
  const binIndex = Math.max(0, Math.min(bins.nBins - 1, Math.floor(fraction * bins.nBins)));

  row.hoveredBin = binIndex;
  drawHistogram(row);

  // Show tooltip
  const tip = $("#hist-tooltip");
  if (!tip) return;

  const lo = bins.edges[binIndex];
  const hi = bins.edges[binIndex + 1];
  const count = bins.counts[binIndex];
  const pct = Math.round((count / bins.total) * 1000) / 10;

  tip.innerHTML = `
    <b>${row.name}</b> · ${formatValue(lo)} – ${formatValue(hi)}<br>
    count <b>${count}</b> (${pct}%)`;
  tip.style.display = "block";
  tip.style.left = Math.min(innerWidth - 260, e.clientX + 14) + "px";
  tip.style.top = Math.min(innerHeight - 80, e.clientY + 14) + "px";
}

// ── Helpers ──────────────────────────────────────────────────────────────

function formatValue(v) {
  if (v == null || !isFinite(v)) return "-";
  return String(Math.round(v * 1e4) / 1e4);
}
