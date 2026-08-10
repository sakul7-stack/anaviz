/**
 * histogram.js — per-channel value-distribution (histogram) panel.
 *
 * One histogram per selected channel (≤ MAX_CHANS), rendered below the
 * heatmap. Each is a small canvas with a drag handle so heights are
 * adjustable independently (persisted in localStorage per channel).
 *
 * Sync: bins are built from the SAME interpolated series the main chart
 * draws (lastRenderData), so the histogram always reflects the visible
 * range. Chart/heatmap hover marks the hovered value on each histogram
 * (via setHistogramHover), and the chart tooltip is enriched with the bin
 * a hovered value falls into (via binInfoLine).
 */

import { $, COLORS, MAX_CHANS } from './utils.js';
import { getValuesAtTime } from './chart.js';

const DEFAULT_H = 72;
const MIN_H     = 36;
const MAX_H     = 260;
const LABEL_H   = 16;    // strip at the bottom of each canvas for value ticks

let _rows  = [];          // { eid, idx, name, color, base, bins, canvas }
let _lastRenderData = null;
let _hover = { t: null, values: null };

/**
 * Rebuild / refresh the histogram rows from the current chart data.
 * @param {Array} selected       channel objects (app state, full list)
 * @param {Object} lastRenderData  chart.js lastRenderData (data, selected, bandByChannel)
 */
export function updateHistograms(selected, lastRenderData) {
  _lastRenderData = lastRenderData;
  const wrap = document.getElementById('hists');
  if (!wrap) return;

  const sel = (selected || []).slice(0, MAX_CHANS);
  const eids = sel.map(c => c.element_id).join(',');
  if (wrap.dataset.eids !== eids) {
    wrap.innerHTML = '';
    wrap.dataset.eids = eids;
    _rows = sel.map((c, i) => _buildRow(wrap, c, i));
  }

  // Row base index into lastRenderData.data for each channel's avg series
  let base = 1;
  _rows.forEach((row, i) => {
    const stride = lastRenderData.bandByChannel?.[i] ? 3 : 1;
    row.base = base;
    base += stride;
  });

  // Rebin from the values the chart actually displays in this range
  _rows.forEach(row => {
    const dataRow = lastRenderData.data?.[row.base];
    const vals = (dataRow || []).filter(v => v !== null && isFinite(v));
    row.bins = _makeBins(vals);
  });

  _drawAll();
}

/** Remove all histograms (e.g. no data in range). */
export function clearHistograms() {
  const wrap = document.getElementById('hists');
  if (wrap) { wrap.innerHTML = ''; wrap.dataset.eids = ''; }
  _rows = [];
  _hover = { t: null, values: null };
}

function _buildRow(wrap, c, i) {
  const row = document.createElement('div');
  row.className = 'hist-row';

  const head = document.createElement('div');
  head.className = 'hist-head';
  const sw = document.createElement('span');
  sw.className = 'legend-swatch';
  sw.style.background = COLORS[i % COLORS.length];
  const name = document.createElement('span');
  name.textContent = c.name.replace(/^ATLAS_/, '');
  name.title = c.name;
  head.appendChild(sw);
  head.appendChild(name);

  const canvas = document.createElement('canvas');
  canvas.className = 'hist-canvas';

  const saved = Number(localStorage.getItem(`histH_${c.element_id}`));
  canvas.style.height = (saved >= MIN_H ? Math.min(saved, MAX_H) : DEFAULT_H) + 'px';

  const resizer = document.createElement('div');
  resizer.className = 'hist-resizer';
  resizer.title = 'Drag to resize histogram · double-click to auto-size';

  row.appendChild(head);
  row.appendChild(canvas);
  row.appendChild(resizer);
  wrap.appendChild(row);

  const entry = {
    eid: c.element_id, idx: i, name: c.name.replace(/^ATLAS_/, ''),
    color: COLORS[i % COLORS.length], base: 1, bins: null, canvas,
    binHover: -1,
  };

  // Per-histogram drag resize
  resizer.addEventListener('mousedown', e => {
    e.preventDefault();
    resizer.classList.add('active');
    const startY = e.clientY;
    const startH = canvas.getBoundingClientRect().height;
    const onMove = ev => {
      const h = Math.min(Math.max(startH + (startY - ev.clientY), MIN_H), MAX_H);
      canvas.style.height = h + 'px';
      localStorage.setItem(`histH_${entry.eid}`, String(Math.round(h)));
      _drawOne(entry);
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup',   onUp);
      resizer.classList.remove('active');
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup',   onUp);
  });
  resizer.addEventListener('dblclick', () => {
    localStorage.removeItem(`histH_${entry.eid}`);
    canvas.style.height = DEFAULT_H + 'px';
    _drawOne(entry);
  });

  // Bin hover tooltip
  canvas.addEventListener('mousemove', e => _onHistMove(entry, e));
  canvas.addEventListener('mouseleave', () => _hideHistTooltip());

  return entry;
}

function _makeBins(vals) {
  const n = vals.length;
  if (!n) return null;
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) {
    if (vals[i] < lo) lo = vals[i];
    if (vals[i] > hi) hi = vals[i];
  }
  if (hi === lo) { hi = lo + 1; lo = lo - 1; }   // single value → one bin
  const nBins = Math.min(60, Math.max(16, Math.round(Math.sqrt(n))));
  const bw = (hi - lo) / nBins;
  const counts = new Array(nBins).fill(0);
  for (let i = 0; i < n; i++) {
    let b = Math.floor((vals[i] - lo) / bw);
    if (b >= nBins) b = nBins - 1;
    counts[b]++;
  }
  const edges = new Array(nBins + 1);
  for (let i = 0; i <= nBins; i++) edges[i] = lo + i * bw;
  return { edges, counts, lo, hi, n: nBins, total: n };
}

function _fmt(v) {
  if (v === null || v === undefined || !isFinite(v)) return '-';
  return String(Math.round(v * 1e4) / 1e4);
}

function _valToX(bins, v) {
  if (!bins || bins.hi <= bins.lo) return 0;
  return ((v - bins.lo) / (bins.hi - bins.lo)) * 100;   // % of width
}

function _drawAll() {
  _rows.forEach(r => _drawOne(r));
}

function _drawOne(row) {
  const cv  = row.canvas;
  if (!cv) return;
  const cssW = cv.parentElement.clientWidth || 400;
  const cssH = cv.offsetHeight || DEFAULT_H;
  if (cv.width !== cssW) cv.width = cssW;
  if (cv.height !== cssH) cv.height = cssH;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cssW, cssH);

  const bins = row.bins;
  const barH = cssH - LABEL_H;
  if (!bins) {
    ctx.fillStyle = '#6c757d';
    ctx.font = '11px system-ui';
    ctx.fillText('no data in range', 8, barH / 2 + 4);
    return;
  }

  const maxC = Math.max(1, ...bins.counts);
  const bwPct = 100 / bins.n;

  for (let b = 0; b < bins.n; b++) {
    const h  = Math.max(1, Math.round((bins.counts[b] / maxC) * (barH - 6)));
    const x  = (bwPct * b) / 100 * cssW;
    const w  = Math.max(1, (bwPct / 100) * cssW - 1);
    ctx.fillStyle = row.color;
    ctx.globalAlpha = 0.45 + 0.55 * (bins.counts[b] / maxC);
    ctx.fillRect(Math.round(x), barH - h, Math.ceil(w), h);
  }
  ctx.globalAlpha = 1;

  // value ticks
  ctx.fillStyle = '#6c757d';
  ctx.font = '10px system-ui';
  ctx.textBaseline = 'top';
  ctx.fillText(_fmt(bins.lo), 2, barH + 3);
  const mid = _fmt((bins.lo + bins.hi) / 2);
  ctx.textAlign = 'center';
  ctx.fillText(mid, cssW / 2, barH + 3);
  ctx.textAlign = 'right';
  ctx.fillText(_fmt(bins.hi), cssW - 2, barH + 3);
  ctx.textAlign = 'left';

  // hovered-value marker (from chart / heatmap hover)
  const v = _hover.values?.[row.idx];
  if (v !== null && v !== undefined && isFinite(v)) {
    const x = (_valToX(bins, v) / 100) * cssW;
    ctx.strokeStyle = '#111';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, 2);
    ctx.lineTo(Math.round(x) + 0.5, barH);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#111';
    ctx.beginPath();
    ctx.arc(Math.round(x), barH, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // hovered bin highlight
  if (row.binHover >= 0) {
    const x  = (bwPct * row.binHover) / 100 * cssW;
    const w  = Math.max(1, (bwPct / 100) * cssW - 1);
    ctx.strokeStyle = '#111';
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.round(x) + 0.5, 1, Math.ceil(w) - 1, barH - 2);
  }
}

function _onHistMove(row, e) {
  const cv = row.canvas;
  const rect = cv.getBoundingClientRect();
  const xPct = (e.clientX - rect.left) / rect.width;
  const bins = row.bins;
  if (!bins) return;
  let b = Math.floor(xPct * bins.n);
  if (b >= bins.n) b = bins.n - 1;
  if (b < 0) b = 0;
  row.binHover = b;
  _drawOne(row);

  const tip = $('#hist-tooltip');
  if (!tip) return;
  const lo = bins.edges[b], hi = bins.edges[b + 1];
  const pct = Math.round((bins.counts[b] / bins.total) * 1000) / 10;
  tip.innerHTML =
    `<b>${row.name}</b> · value ${_fmt(lo)} – ${_fmt(hi)}<br>` +
    `count <b>${bins.counts[b]}</b> (${pct}%)`;
  tip.style.display = 'block';
  tip.style.left = Math.min(window.innerWidth  - 260, e.clientX + 14) + 'px';
  tip.style.top  = Math.min(window.innerHeight - 80,  e.clientY + 14) + 'px';
}

function _hideHistTooltip() {
  _rows.forEach(r => r.binHover = -1);
  const tip = $('#hist-tooltip');
  if (tip) tip.style.display = 'none';
  _drawAll();
}

/**
 * Mark the hovered time on every histogram (vertical value marker).
 * Called from the chart AND heatmap hover sync. Pass null to clear.
 */
export function setHistogramHover(t) {
  _hover.t = t;
  if (t == null) {
    _hover.values = null;
    _drawAll();
    return;
  }
  const got = _lastRenderData ? getValuesAtTime(t) : null;
  _hover.values = got ? got.values : null;
  _drawAll();
}

/** Clear markers + tooltip (chart mouseleave). */
export function clearHistogramHover() {
  _hover = { t: null, values: null };
  _rows.forEach(r => r.binHover = -1);
  _hideHistTooltip();
}

/**
 * Extra tooltip line for the chart tooltip: which bin a hovered value falls
 * into for that channel. Returns '' when histograms are hidden/no bins.
 */
export function binInfoLine(channelIdx, value) {
  const row = _rows[channelIdx];
  if (!row?.bins || value === null || value === undefined || !isFinite(value)) return '';
  const { edges, counts, lo, hi } = row.bins;
  let b = -1;
  for (let i = 0; i < edges.length - 1; i++) {
    if (value >= edges[i] && value < edges[i + 1]) { b = i; break; }
  }
  if (b < 0) b = value >= hi ? edges.length - 2 : 0;
  const pct = Math.round((counts[b] / row.bins.total) * 1000) / 10;
  return `<div class="tt-row">
    <span class="tt-swatch" style="background:${row.color}"></span>
    <span class="tt-name">bin ${_fmt(edges[b])} – ${_fmt(edges[b + 1])}</span>
    <span class="tt-val">${counts[b]} pts</span>
  </div>`;
}
