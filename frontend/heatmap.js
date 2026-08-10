/**
 * heatmap.js — canvas-based cross-channel heatmap renderer.
 *
 * Visual encoding:
 *   score 0.0 (z ≈ -3, below normal) → blue  rgb(0,  20, 180)
 *   score 0.5 (z =  0, normal)        → dark  rgb(10, 10,  10)
 *   score 1.0 (z ≈ +3, anomalous)     → red   rgb(220,20,  0)
 *
 * Features: channel names on the Y axis, time labels on the X axis,
 * hover crosshair + tooltip (time · channel · z-score · graph values),
 * and click-to-jump (recenters the main chart on the clicked timestamp).
 *
 * SYNC: every column is positioned by TIME through the live chart's own
 * x-scale (uPlot.valToPos), so the heatmap is pixel-aligned with the graph
 * even while zooming (stale buckets map to their true time positions) and
 * never drifts after chart rebuilds/resizes. `redrawHeatmap()` is wired to
 * the chart's draw hook in app.js so any chart redraw re-syncs this view.
 *
 * Height: a drag handle (#heatmap-resizer) above the panel resizes it; the
 * choice persists in localStorage. Double-click the handle to auto-size.
 */

import {
  plot as mainPlot,
  getPlotGeometry,
  xForTime,
} from './chart.js';

let _canvas     = null;
let _lastData   = null;
let _nameMap    = {};
let _onSeek     = null;
let _onHover    = null;
let _tooltipExtra = null;   // (unixTs) → extra HTML appended to the tooltip
let _hover      = { col: -1, row: -1 };
let _raf        = null;
let _renderedData = null;   // last _lastData actually painted
let _lastKey    = null;     // last geometry+x-range painted

/**
 * Bind the heatmap to a canvas element.
 * Call once on page load.
 * @param {HTMLCanvasElement} canvas
 * @param {Object} [opts]  { onSeek: (unixTs) => void, onHover: (unixTs|null) => void }
 */
export function initHeatmap(canvas, opts = {}) {
  _canvas = canvas;
  _onSeek = opts.onSeek || null;
  _onHover = opts.onHover || null;
  canvas.addEventListener('mousemove', e => _onMove(e));
  canvas.addEventListener('mouseleave', () => {
    _hover = { col: -1, row: -1 };
    _hideTooltip();
    if (_onHover) _onHover(null);
    _render();
  });
  canvas.addEventListener('click', e => {
    if (!_lastData || _onSeek === null) return;
    const col = _colAt(e.offsetX);
    if (col >= 0) _onSeek(_lastData.t[col]);
  });

  const resizer = document.getElementById('heatmap-resizer');
  if (resizer) {
    resizer.addEventListener('mousedown', e => {
      e.preventDefault();
      resizer.classList.add('active');
      const plotEl = canvas.parentElement;
      const startY = e.clientY;
      const startH = plotEl.getBoundingClientRect().height;
      const onMove = ev => {
        const h = Math.min(Math.max(startH + (startY - ev.clientY), 48), 520);
        plotEl.style.height = h + 'px';
        localStorage.setItem('hmHeight', String(Math.round(h)));
        redrawHeatmap();
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
      localStorage.removeItem('hmHeight');
      if (_lastData) { _sizeToRows(_lastData.channels.length); redrawHeatmap(); }
    });
  }
}

/** Register a callback (unixTs) → extra tooltip HTML (graph values at that time). */
export function setHeatmapTooltipExtra(fn) { _tooltipExtra = fn; }

/**
 * Render a new heatmap frame.
 * @param {Object} data   Result from GET /api/heatmap
 *   { t: float[], channels: int[], scores: float[][] }
 * @param {Object} nameMap  element_id → display name
 */
export function updateHeatmap(data, nameMap = {}) {
  if (!_canvas || !data?.t?.length || !data?.channels?.length) {
    _clearYAxis();
    _clearXAxis();
    return;
  }
  _nameMap = nameMap;
  _lastData = data;
  _renderedData = null;   // force a repaint even if geometry is unchanged
  _hover = { col: -1, row: -1 };
  _sizeToRows(data.channels.length);
  // Wait one frame so the panel has laid out (real offsetWidth/Height),
  // otherwise the first paint uses the fallback pixel size.
  requestAnimationFrame(() => { if (_lastData === data) _render(); });
}

// Current geometry + visible x-range, as a comparable key. The x-range is
// part of the key because zooming changes the time→pixel mapping WITHOUT
// changing gut/plotW — those are exactly the repaints we must not skip.
function _geomKey() {
  const g = getPlotGeometry();
  let xmin = null, xmax = null;
  if (mainPlot?.scales?.x) {
    xmin = mainPlot.scales.x.min ?? null;
    xmax = mainPlot.scales.x.max ?? null;
  }
  return { cssW: g.cssW, gut: g.gut, plotW: g.plotW, xmin, xmax };
}

/**
 * Re-render the heatmap with the CURRENT chart geometry (rAF-coalesced).
 * Called from the chart's draw hook so zoom/pan/resize/rebuild always
 * re-sync this view — no need to toggle the panel off/on anymore.
 * No-ops when neither the data nor the geometry/x-range changed (e.g. the
 * chart's hover-cursor redraws), so hovering/panning stays cheap.
 */
export function redrawHeatmap() {
  if (!_lastData || _raf) return;
  const key = _geomKey();
  if (_renderedData === _lastData && _lastKey &&
      key.cssW === _lastKey.cssW && key.gut === _lastKey.gut &&
      key.plotW === _lastKey.plotW && key.xmin === _lastKey.xmin &&
      key.xmax === _lastKey.xmax) {
    return;
  }
  _raf = requestAnimationFrame(() => {
    _raf = null;
    if (_lastData) _render();
  });
}

function _sizeToRows(nRows) {
  const plotEl = _canvas.parentElement;
  if (!plotEl) return;
  const saved = Number(localStorage.getItem('hmHeight'));
  if (saved >= 48) {
    plotEl.style.height = saved + 'px';
    return;
  }
  plotEl.style.height = Math.min(Math.max(nRows * 13, 56), 176) + 'px';
}

// Time range covered by the current heatmap data (for the no-plot fallback).
function _dataRange() {
  if (!_lastData?.t?.length) return null;
  return { t0: _lastData.t[0], t1: _lastData.t[_lastData.t.length - 1] };
}

// Column i pixel span [x0, x1) measured from the canvas left edge, derived
// from the bucket's time edges mapped through the LIVE chart x-scale.
function _colSpan(i, geo) {
  const data = _lastData;
  const n    = data.t.length;
  const range  = _dataRange();
  let lo, hi;
  if (n > 1) {
    const dt = (data.t[n - 1] - data.t[0]) / (n - 1);
    lo = data.t[i] - dt / 2;
    hi = data.t[i] + dt / 2;
  } else {
    lo = hi = data.t[i];
  }
  return {
    x0: geo.gut + xForTime(lo, range),
    x1: geo.gut + xForTime(hi, range),
  };
}

function _render() {
  const data     = _lastData;
  const nCols    = data.t.length;
  const nRows    = data.channels.length;
  const ctx      = _canvas.getContext('2d');
  const geo      = getPlotGeometry();
  const { cssW, gut, plotW } = geo;

  // Pin the canvas CSS width to the chart width so the time axis spans the
  // exact same pixels as the graph (equal even in narrow windows where the
  // chart forces a minimum width).
  if (cssW > 0 && _canvas.style.width !== cssW + 'px') {
    _canvas.style.width = cssW + 'px';
    document.getElementById('heatmap-xaxis')?.style.setProperty('width', cssW + 'px');
  }

  // Match canvas pixel size to its CSS layout size
  const cssH = _canvas.offsetHeight || 160;
  if (_canvas.width  !== cssW) _canvas.width  = cssW;
  if (_canvas.height !== cssH) _canvas.height = cssH;

  const cellH = Math.max(1, cssH / nRows);
  const plotR = gut + plotW;

  ctx.clearRect(0, 0, cssW, cssH);

  // Pre-compute each column's pixel span (time → pixels via the live chart)
  const spans = new Array(nCols);
  for (let c = 0; c < nCols; c++) spans[c] = _colSpan(c, geo);

  // Fill cells in two passes: base colours first, then a highlight overlay
  // for the hovered column so it stands out without repainting everything.
  for (let r = 0; r < nRows; r++) {
    const row = data.scores[r];
    for (let c = 0; c < nCols; c++) {
      const s = spans[c];
      const x0 = Math.max(gut, Math.round(s.x0));
      const x1 = Math.min(plotR, Math.round(s.x1));
      if (x1 <= x0) continue;                 // column is off-screen (zoomed out / stale data)
      const score = row?.[c] ?? 0;
      ctx.fillStyle = _scoreToColor(score);
      ctx.fillRect(x0, Math.round(r * cellH), x1 - x0, Math.ceil(cellH));
    }
  }

  if (_hover.col >= 0 && _hover.col < nCols) {
    const s = spans[_hover.col];
    const x0 = Math.max(gut, Math.round(s.x0));
    const x1 = Math.min(plotR, Math.round(s.x1));
    if (x1 > x0) {
      ctx.fillStyle = 'rgba(255,255,255,0.18)';
      ctx.fillRect(x0, 0, x1 - x0, cssH);
    }
  }

  _renderYAxis(data.channels, nRows, cssH, cellH);
  _renderXAxis(data.t, nCols, cssW, gut, plotW, spans);

  _renderedData = data;
  _lastKey = _geomKey();
}

/** Time under a canvas x (CSS px from the canvas left edge). */
function _timeAt(x) {
  if (!_lastData) return null;
  const g = getPlotGeometry();
  const range = _dataRange();
  if (mainPlot) {
    const t = mainPlot.posToVal(x - g.gut, 'x');
    if (isFinite(t)) return t;
  }
  if (range && range.t1 > range.t0 && x >= g.gut) {
    return range.t0 + ((x - g.gut) / g.plotW) * (range.t1 - range.t0);
  }
  return null;
}

function _colAt(x) {
  if (!_lastData || !_lastData.t.length) return -1;
  const t = _timeAt(x);
  if (t == null) return -1;
  const arr = _lastData.t;
  let lo = 0, hi = arr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < t) lo = mid; else hi = mid;
  }
  return Math.abs(arr[lo] - t) <= Math.abs(arr[hi] - t) ? lo : hi;
}

function _onMove(e) {
  if (!_lastData) return;
  const col = _colAt(e.offsetX);
  const cssH = _canvas.offsetHeight || 160;
  const cellH = cssH / _lastData.channels.length;
  const row = Math.min(_lastData.channels.length - 1, Math.floor(e.offsetY / cellH));
  if (col !== _hover.col) {
    _hover = { col, row };
    _render();
  } else {
    _hover.row = row;
  }
  _showTooltip(e, col, row);
  if (_onHover) _onHover(col >= 0 ? _lastData.t[col] : null);
}

/**
 * Highlight the heatmap column at the given timestamp (called from the main
 * chart's hover sync). Pass null to clear. No tooltip — the chart tooltip is
 * the one showing details at that point.
 * @param {number|null} t  unix timestamp
 */
export function highlightColumn(t) {
  if (!_lastData) return;
  const nCols = _lastData.t.length;
  if (t == null || nCols === 0) {
    _hover = { col: -1, row: -1 };
    _render();
    return;
  }
  // binary search nearest bucket centre
  const arr = _lastData.t;
  let lo = 0, hi = nCols - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < t) lo = mid; else hi = mid;
  }
  const col = Math.abs(arr[lo] - t) <= Math.abs(arr[hi] - t) ? lo : hi;
  _hover = { col, row: -1 };
  _render();
}

function _showTooltip(e, col, row) {
  const data = _lastData;
  const tip  = document.getElementById('hm-tooltip');
  if (!tip || col < 0 || row < 0 || !data?.t?.[col]) { _hideTooltip(); return; }
  const score = data.scores?.[row]?.[col];
  if (score === undefined) { _hideTooltip(); return; }
  const z     = (score * 6 - 3).toFixed(2);
  const cid   = data.channels[row];
  const name  = _nameMap[cid] || `ch ${cid}`;
  const ts    = new Date(data.t[col] * 1000).toLocaleString();
  let html = `<b>${name}</b> · ${ts}<br>z-score <b>${z}</b>`;
  // Show the same per-channel values the main chart tooltip would show at
  // this time, so the heatmap hover carries the graph's info too.
  if (_tooltipExtra) {
    const extra = _tooltipExtra(data.t[col]);
    if (extra) html += `<div class="hm-extra">${extra}</div>`;
  }
  tip.innerHTML = html;
  tip.style.display = 'block';
  tip.style.left = Math.min(window.innerWidth  - 260, e.clientX + 14) + 'px';
  tip.style.top  = Math.min(window.innerHeight - 90,  e.clientY + 14) + 'px';
}

function _hideTooltip() {
  const tip = document.getElementById('hm-tooltip');
  if (tip) tip.style.display = 'none';
}

/**
 * Map a normalised score [0, 1] to a CSS color string.
 * 0 → blue, 0.5 → near-black, 1 → red
 */
function _scoreToColor(score) {
  const s = Math.max(0, Math.min(1, score));
  if (s < 0.5) {
    // Blue → dark
    const t   = s / 0.5;
    const red = Math.round(t * 10);
    const grn = Math.round(t * 10);
    const blu = Math.round(180 - t * 170);
    return `rgb(${red},${grn},${blu})`;
  } else {
    // Dark → red
    const t   = (s - 0.5) / 0.5;
    const red = Math.round(10 + t * 210);
    const grn = Math.round(10 - t * 10);
    const blu = Math.round(10 - t * 10);
    return `rgb(${red},${grn},${blu})`;
  }
}

function _renderYAxis(channelIds, nRows, cssH, cellH) {
  const yaxis = document.getElementById('heatmap-yaxis');
  if (!yaxis) return;
  yaxis.innerHTML = '';
  yaxis.style.height = cssH + 'px';

  // Only label every Nth row so they don't overlap
  const labelH  = 12;                         // px per label minimum
  const step    = Math.max(1, Math.ceil(labelH / cellH));

  for (let r = 0; r < nRows; r += step) {
    const cid   = channelIds[r];
    const label = document.createElement('div');
    label.className = 'hm-label';
    label.style.top = Math.round(r * cellH + cellH / 2) + 'px';
    const name = _nameMap[cid];
    label.textContent = name || `ch ${cid}`;
    label.title = `element_id ${cid}`;
    yaxis.appendChild(label);
  }
}

function _renderXAxis(tArr, nCols, cssW, gut, plotW, spans) {
  const xaxis = document.getElementById('heatmap-xaxis');
  if (!xaxis) return;
  xaxis.innerHTML = '';
  xaxis.style.width = cssW + 'px';

  const t0 = tArr[0], t1 = tArr[nCols - 1];
  const span = t1 - t0;
  const nLabels = Math.max(2, Math.min(6, Math.floor(cssW / 130)));
  for (let i = 0; i < nLabels; i++) {
    const f  = i / (nLabels - 1);
    const ts = t0 + span * f;
    const col = Math.min(nCols - 1, Math.round(f * (nCols - 1)));
    const label = document.createElement('div');
    label.className = 'hm-xlabel';
    // Position by the column's true pixel span (time-mapped) so labels stay
    // glued to the data even while the chart is zoomed.
    const s = spans[col];
    label.style.left = Math.round((s.x0 + s.x1) / 2) + 'px';
    label.textContent = new Date(ts * 1000).toLocaleString();
    xaxis.appendChild(label);
  }
}

function _clearYAxis() {
  const yaxis = document.getElementById('heatmap-yaxis');
  if (yaxis) yaxis.innerHTML = '';
}

function _clearXAxis() {
  const xaxis = document.getElementById('heatmap-xaxis');
  if (xaxis) xaxis.innerHTML = '';
  if (_canvas) {
    const ctx = _canvas.getContext('2d');
    ctx.clearRect(0, 0, _canvas.width, _canvas.height);
  }
}
