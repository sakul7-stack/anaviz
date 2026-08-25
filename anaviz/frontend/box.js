/**
 * box.js — Per-entity box plot (median · IQR · whiskers · outliers).
 *
 * One horizontal box plot per selected entity on a shared value scale,
 * computed from the SAME observed values the histograms use, so both
 * distribution views always agree.
 */
import { COLORS, MAX_SERIES } from "./utils.js";
import { niceTicks, formatValue } from "./histogram.js";

// Constants

const BOX_HEIGHT = 44;
const LABEL_HEIGHT = 14;

// State

let rows = [];              // [{ entityId, canvas, stats }]
let lastRenderData = null;
let hoverState = null;      // row currently hovered (for tooltip)

// Public API

/**
 * Rebuild or refresh the box plots from the current chart data.
 */
export function updateBoxes(selected, renderData) {
  lastRenderData = renderData;

  const wrap = document.getElementById("boxes");
  if (!wrap) return;

  const entities = (selected || []).slice(0, MAX_SERIES);
  const entityIds = entities.map((e) => e.entity_id).join(",");

  if (wrap.dataset.eids !== entityIds) {
    wrap.innerHTML = "";
    wrap.dataset.eids = entityIds;
    rows = entities.map((entity, i) =>
      buildRow(wrap, entity, i));
  }

  rows.forEach((row, i) => {
    const observedValues = renderData?.entityMeta?.[i]?.observed;
    const values = (observedValues || [])
      .filter((v) => v != null && isFinite(v));

    row.stats = values.length ? computeBoxStats(values) : null;
    drawBox(row);
  });
}

/** Remove all box plots. */
export function clearBoxes() {
  const wrap = document.getElementById("boxes");
  if (wrap) {
    wrap.innerHTML = "";
    wrap.dataset.eids = "";
  }
  rows = [];
  lastRenderData = null;
  hideTooltip();
}

/** Snapshot of current box statistics for CSV export. */
export function getBoxesExport() {
  return rows
    .filter((row) => row.stats)
    .map((row) => ({ name: row.name, stats: { ...row.stats } }));
}

// Build a box plot row

function buildRow(wrap, entity, index) {
  const rowEl = document.createElement("div");
  rowEl.className = "box-row";

  const header = document.createElement("div");
  header.className = "hist-head";   // reuse histogram header styling

  const swatch = document.createElement("span");
  swatch.className = "legend-swatch";
  swatch.style.background = COLORS[index % COLORS.length];

  const nameEl = document.createElement("span");
  nameEl.textContent = entity.label || entity.name;
  header.append(swatch, nameEl);

  const canvasEl = document.createElement("canvas");
  canvasEl.className = "box-canvas";
  canvasEl.style.height = BOX_HEIGHT + LABEL_HEIGHT + "px";

  rowEl.append(header, canvasEl);
  wrap.appendChild(rowEl);

  canvasEl.addEventListener("mousemove", (e) => handleHover(canvasEl, e));
  canvasEl.addEventListener("mouseleave", () => {
    hoverState = null;
    hideTooltip();
  });

  return {
    entityId: entity.entity_id,
    index,
    name: entity.label || entity.name,
    color: COLORS[index % COLORS.length],
    canvas: canvasEl,
    stats: null,
  };
}

// Statistics

/**
 * Tukey box-plot statistics: quartiles (linear interpolation),
 * 1.5·IQR fences, whiskers at the most extreme inlier points, outliers.
 */
function computeBoxStats(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;

  const quantile = (p) => {
    const pos = (n - 1) * p;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  };

  const q1 = quantile(0.25);
  const med = quantile(0.5);
  const q3 = quantile(0.75);
  const iqr = q3 - q1;
  const loFence = q1 - 1.5 * iqr;
  const hiFence = q3 + 1.5 * iqr;

  let wLo = sorted[0];
  let wHi = sorted[n - 1];
  for (const v of sorted) { if (v >= loFence) { wLo = v; break; } }
  for (let i = n - 1; i >= 0; i--) {
    if (sorted[i] <= hiFence) { wHi = sorted[i]; break; }
  }

  const outliers = iqr > 0
    ? sorted.filter((v) => v < loFence || v > hiFence)
    : [];

  return { min: sorted[0], max: sorted[n - 1], q1, med, q3, wLo, wHi, outliers, n };
}

// Drawing

function drawBox(row) {
  const cv = row.canvas;
  if (!cv) return;

  const cssWidth = cv.parentElement.clientWidth || 400;
  const cssHeight = cv.offsetHeight || BOX_HEIGHT + LABEL_HEIGHT;
  if (cv.width !== cssWidth) cv.width = cssWidth;
  if (cv.height !== cssHeight) cv.height = cssHeight;

  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const stats = row.stats;
  if (!stats) {
    ctx.fillStyle = "#6c757d";
    ctx.font = "11px system-ui";
    ctx.fillText("no data", 8, BOX_HEIGHT / 2 + 4);
    return;
  }

  // Value scale with a little padding
  const span0 = stats.max - stats.min || 1;
  const min = stats.min - span0 * 0.04;
  const max = stats.max + span0 * 0.04;
  const x = (v) => ((v - min) / (max - min)) * cssWidth;

  const cy = BOX_HEIGHT / 2;
  const boxTop = cy - 11;
  const boxBottom = cy + 11;

  const xQ1 = x(stats.q1);
  const xQ3 = x(stats.q3);
  const xMed = x(stats.med);
  const xWLo = x(stats.wLo);
  const xWHi = x(stats.wHi);

  ctx.lineWidth = 1;

  // Whiskers
  ctx.strokeStyle = row.color;
  ctx.beginPath();
  ctx.moveTo(Math.round(xWLo) + 0.5, cy);
  ctx.lineTo(Math.round(xQ1) + 0.5, cy);
  ctx.moveTo(Math.round(xQ3) + 0.5, cy);
  ctx.lineTo(Math.round(xWHi) + 0.5, cy);
  // Whisker caps
  ctx.moveTo(Math.round(xWLo) + 0.5, boxTop + 3);
  ctx.lineTo(Math.round(xWLo) + 0.5, boxBottom - 3);
  ctx.moveTo(Math.round(xWHi) + 0.5, boxTop + 3);
  ctx.lineTo(Math.round(xWHi) + 0.5, boxBottom - 3);
  ctx.stroke();

  // Box (IQR)
  const boxWidth = Math.max(xQ3 - xQ1, 2);
  ctx.globalAlpha = 0.35;
  ctx.fillStyle = row.color;
  ctx.fillRect(xQ1, boxTop, boxWidth, boxBottom - boxTop);
  ctx.globalAlpha = 1;
  ctx.strokeRect(Math.round(xQ1) + 0.5, boxTop + 0.5, Math.round(boxWidth), boxBottom - boxTop - 1);

  // Median line
  ctx.strokeStyle = "#111111";
  ctx.beginPath();
  ctx.moveTo(Math.round(xMed) + 0.5, boxTop);
  ctx.lineTo(Math.round(xMed) + 0.5, boxBottom);
  ctx.stroke();

  // Outliers
  ctx.fillStyle = row.color;
  for (const v of stats.outliers) {
    ctx.globalAlpha = 0.6;
    ctx.beginPath();
    ctx.arc(x(v), cy, 2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  // Hover highlight of the whole strip
  if (hoverState === row) {
    ctx.strokeStyle = "rgba(0,0,0,0.15)";
    ctx.strokeRect(0.5, 0.5, cssWidth - 1, BOX_HEIGHT - 1);
  }

  // Value ticks at bottom
  ctx.fillStyle = "#6c757d";
  ctx.font = "10px system-ui";
  ctx.textBaseline = "top";
  ctx.textAlign = "center";

  const targetTicks = Math.max(2, Math.min(6, Math.floor(cssWidth / 110)));
  let prevLabel = null;
  for (const t of niceTicks(stats.min, stats.max, targetTicks)) {
    const label = formatValue(t);
    if (label === prevLabel) continue;
    prevLabel = label;
    const w = ctx.measureText(label).width;
    const tx = Math.max(w / 2 + 1, Math.min(cssWidth - w / 2 - 1, x(t)));
    ctx.fillText(label, tx, BOX_HEIGHT + 2);
  }
  ctx.textAlign = "left";
}

// Hover

function handleHover(canvasEl, e) {
  const row = rows.find((r) => r.canvas === canvasEl);
  if (!row?.stats) return;

  hoverState = row;
  drawBox(row);

  const s = row.stats;
  const tip = document.getElementById("box-tooltip");
  if (!tip) return;

  tip.innerHTML = `
    <b>${row.name}</b> · n=${s.n}<br>
    whisk ${formatValue(s.wLo)} – ${formatValue(s.wHi)}<br>
    q1 <b>${formatValue(s.q1)}</b> · med <b>${formatValue(s.med)}</b> · q3 <b>${formatValue(s.q3)}</b><br>
    IQR ${formatValue(s.q3 - s.q1)} · outliers <b>${s.outliers.length}</b>`;
  tip.style.display = "block";
  tip.style.left = Math.min(innerWidth - 240, e.clientX + 14) + "px";
  tip.style.top = Math.min(innerHeight - 100, e.clientY + 14) + "px";
}

function hideTooltip() {
  const tip = document.getElementById("box-tooltip");
  if (tip) tip.style.display = "none";
}
