/**
 * heatmap.js — canvas-based cross-channel heatmap renderer.
 *
 * Visual encoding:
 *   score 0.0 (z ≈ -3, below normal) → blue  rgb(0,  20, 180)
 *   score 0.5 (z =  0, normal)        → dark  rgb(10, 10,  10)
 *   score 1.0 (z ≈ +3, anomalous)     → red   rgb(220,20,  0)
 *
 * Features: channel names on the Y axis, time labels on the X axis,
 * hover crosshair + tooltip (time · channel · z-score), and click-to-jump
 * (recenters the main chart on the clicked timestamp). The time axis is
 * pixel-aligned with the main chart (same canvas width, same bbox gutter),
 * so hovering one view highlights the matching position in the other.
 *
 * Height: a drag handle (#heatmap-resizer) above the panel resizes it; the
 * choice persists in localStorage. Double-click the handle to auto-size.
 */

import { plot as mainPlot } from './chart.js';

let _canvas   = null;
let _lastData = null;
let _nameMap  = {};
let _onSeek   = null;
let _onHover  = null;
let _hover    = { col: -1, row: -1 };

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
        requestAnimationFrame(_render);
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
      if (_lastData) { _sizeToRows(_lastData.channels.length); requestAnimationFrame(_render); }
    });
  }
}

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
  _hover = { col: -1, row: -1 };
  _sizeToRows(data.channels.length);
  // Wait one frame so the panel has laid out (real offsetWidth/Height),
  // otherwise the first paint uses the fallback pixel size.
  requestAnimationFrame(() => { if (_lastData === data) _render(); });
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

// Canvas-left CSS pixel geometry shared with the main chart: the time axis
// starts at the chart's bbox.left (its y-axis gutter) and spans bbox.width.
function _geometry() {
  const cssW = _canvas.offsetWidth || 800;
  const dpr  = window.devicePixelRatio || 1;
  let gut = 0, plotW = cssW;
  if (mainPlot?.bbox?.width) {
    gut   = (mainPlot.bbox.left   || 0) / dpr;
    plotW = mainPlot.bbox.width         / dpr;
  }
  return { cssW, gut, plotW };
}

function _render() {
  const data     = _lastData;
  const nCols    = data.t.length;
  const nRows    = data.channels.length;
  const ctx      = _canvas.getContext('2d');
  const { cssW, gut, plotW } = _geometry();

  // Match canvas pixel size to its CSS layout size
  const cssH = _canvas.offsetHeight || 160;
  if (_canvas.width  !== cssW) _canvas.width  = cssW;
  if (_canvas.height !== cssH) _canvas.height = cssH;

  const cellW = plotW / nCols;
  const cellH = Math.max(1, cssH / nRows);

  ctx.clearRect(0, 0, cssW, cssH);

  // Fill cells in two passes: base colours first, then a highlight overlay
  // for the hovered column so it stands out without repainting everything.
  for (let r = 0; r < nRows; r++) {
    const row = data.scores[r];
    for (let c = 0; c < nCols; c++) {
      const score = row?.[c] ?? 0;
      ctx.fillStyle = _scoreToColor(score);
      ctx.fillRect(
        Math.round(gut + c * cellW),
        Math.round(r * cellH),
        Math.ceil(cellW),
        Math.ceil(cellH)
      );
    }
  }

  if (_hover.col >= 0 && _hover.col < nCols) {
    ctx.fillStyle = 'rgba(255,255,255,0.18)';
    ctx.fillRect(
      Math.round(gut + _hover.col * cellW), 0,
      Math.ceil(cellW), cssH
    );
  }

  _renderYAxis(data.channels, nRows, cssH, cellH);
  _renderXAxis(data.t, nCols, cssW, gut, cellW);
}

function _colAt(x) {
  if (!_lastData || !_lastData.t.length) return -1;
  const { gut, plotW } = _geometry();
  if (x < gut) return -1;
  const cellW = plotW / _lastData.t.length;
  return Math.min(_lastData.t.length - 1, Math.floor((x - gut) / cellW));
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
  let lo = 0, hi = nCols - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (_lastData.t[mid] < t) lo = mid; else hi = mid;
  }
  const col = Math.abs(_lastData.t[lo] - t) <= Math.abs(_lastData.t[hi] - t)
    ? lo : hi;
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
  tip.innerHTML = `<b>${name}</b> · ${ts}<br>z-score <b>${z}</b>`;
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

function _renderXAxis(tArr, nCols, cssW, gut, cellW) {
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
    label.style.left = Math.round(gut + col * cellW + cellW / 2) + 'px';
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
