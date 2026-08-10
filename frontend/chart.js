/**
 * chart.js — uPlot lifecycle, SPOT score panel, hover tooltip.
 */
import { $, COLORS, MAX_CHANS, nearestIndex } from './utils.js';

export let plot      = null;
export let scorePlot = null;
export let lastRenderData = null;
let zoom = null;

let _syncT     = null;   // shared time cursor (set from the heatmap)
let _hoverSync = null;   // callback for chart-hover → heatmap sync
let _chartDraw = null;   // callback invoked after every chart redraw (zoom/pan/resize/toggle)
let _tooltipExtra = null; // callback (channelIdx, value) → extra tooltip HTML (histogram bin info)

// ── Export / reset ────────────────────────────────────────────────────────────
export function destroyPlot() {
  if (plot)      { plot.destroy();      plot      = null; }
  if (scorePlot) { scorePlot.destroy(); scorePlot = null; }
}

export function setZoom(t0, t1) { zoom = [t0, t1]; }
export function clearZoom()     { zoom = null; }

/**
 * Geometry of the main chart plot area in CSS pixels, measured from the
 * chart's left edge: `gut` is the y-axis gutter, `plotW` the plot width.
 * Derived from the LIVE plot on every call (device-pixel safe: uses the
 * canvas backing-store scale, not window.devicePixelRatio), so it can never
 * go stale — this is the single source of truth for heatmap/histogram sync.
 */
export function getPlotGeometry() {
  const wrap = document.getElementById('chart');
  const cssW = plot ? plot.width
                    : Math.max(400, wrap ? wrap.clientWidth : 400);
  if (!plot || !plot.bbox?.width) return { cssW, gut: 0, plotW: cssW, hasPlot: !!plot };
  const scale = (plot.ctx.canvas.width || 0) / (plot.width || 1) || 1;
  return {
    cssW,
    gut:   plot.bbox.left   / scale,
    plotW: plot.bbox.width  / scale,
    hasPlot: true,
  };
}

/**
 * CSS-pixel x position (plot-relative, i.e. 0..plotW inside the plot area)
 * for a unix timestamp, mapped through the live chart's x-scale.
 * Falls back to a linear map over `range` (heatmap's own data range) when
 * there is no plot yet.
 */
export function xForTime(t, range = null) {
  if (plot) {
    const p = plot.valToPos(t, 'x', false);
    if (isFinite(p)) return p;
  }
  if (range && range.t1 > range.t0) {
    const g = getPlotGeometry();
    return ((t - range.t0) / (range.t1 - range.t0)) * g.plotW;
  }
  return 0;
}

/**
 * Per-channel values (aligned to lastRenderData.selected) at the nearest
 * data point to time `t` — used by the heatmap/histogram tooltips so they
 * can show the same info as the main chart. Returns null if no data.
 */
export function getValuesAtTime(t) {
  const lr = lastRenderData;
  if (!lr?.data?.[0]?.length) return null;
  const tArr = lr.data[0];
  const idx  = nearestIndex(tArr, t);
  if (idx < 0) return null;
  const values = [];
  let base = 1;
  lr.selected.forEach((c, i) => {
    const stride = lr.bandByChannel?.[i] ? 3 : 1;
    values.push(lr.data[base]?.[idx] ?? null);
    base += stride;
  });
  return { t: tArr[idx], values, selected: lr.selected };
}

/** Register a callback fired after every chart redraw (rAF-coalesce it yourself). */
export function setChartDrawSync(fn) { _chartDraw = fn; }

/** Register a callback (channelIdx, value) → extra tooltip HTML for the chart tooltip. */
export function setTooltipEnricher(fn) { _tooltipExtra = fn; }

/**
 * Draw a vertical cursor on the main chart at the given time (or clear it).
 * Called when the heatmap is hovered, to sync the two views.
 */
export function setChartCursor(t) {
  _syncT = t;
  if (plot) plot.redraw();
}

/** Register a callback invoked with the hovered timestamp (or null) from the
 *  main chart, so the heatmap column can follow it. */
export function setHoverSync(fn) { _hoverSync = fn; }

// ── Main chart init ───────────────────────────────────────────────────────────
export function initPlot(data, selected, onNav, perChannelAxes, bandByChannel) {
  const wrap = document.getElementById('chart');
  wrap.innerHTML = '';
  const w = Math.max(400, document.getElementById('chartwrap').clientWidth);
  const h = Math.max(240, document.getElementById('chartwrap').clientHeight - 20);

  const series = [{}];
  const bands  = [];
  let   axes, scales;

  if (perChannelAxes) {
    scales = { x: zoom ? { min: zoom[0], max: zoom[1], time: true } : { time: true } };
    axes   = [{ space: 50 }];
    selected.slice(0, MAX_CHANS).forEach((s, i) => {
      const scaleKey = i === 0 ? 'y' : `y${i}`;
      scales[scaleKey] = {};
      const color = COLORS[i % COLORS.length];
      const base  = series.length;
      series.push({ label: s.name.replace(/^ATLAS_/, ''), stroke: color,
                    width: 1.5, fill: null, scale: scaleKey });
      if (bandByChannel?.[i]) {
        series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false }, scale: scaleKey });
        series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false }, scale: scaleKey });
        bands.push({ series: [base + 2, base + 1], fill: color + '26' });
      }
      axes.push({ scale: scaleKey, side: i % 2 === 0 ? 3 : 1,
                  grid: { show: i % 2 === 0 }, size: 70, stroke: color });
    });
  } else {
    selected.slice(0, MAX_CHANS).forEach((s, i) => {
      const color = COLORS[i % COLORS.length];
      const base  = series.length;
      series.push({ label: s.name.replace(/^ATLAS_/, ''), stroke: color,
                    width: 1.5, fill: null });
      if (bandByChannel?.[i]) {
        series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
        series.push({ stroke: 'rgba(0,0,0,0)', points: { show: false } });
        bands.push({ series: [base + 2, base + 1], fill: color + '26' });
      }
    });
    axes   = [{ space: 50 }, { grid: { show: true }, size: 70 }];
    scales = { x: zoom ? { min: zoom[0], max: zoom[1], time: true } : { time: true } };
  }

  const drawSync = (u) => {
    if (_syncT == null) return;
    const x = Math.round(u.valToPos(_syncT, 'x', true));
    if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) return;
    const { top, height } = u.bbox;
    const ctx = u.ctx;
    ctx.save();
    ctx.strokeStyle = 'rgba(0,0,0,0.35)';
    ctx.lineWidth   = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, top + height);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  };

  const opts = {
    width: w, height: h,
    series, axes, scales, bands,
    cursor: { y: true, x: false },
    dblclick: false,
    hooks: { draw: [drawSync, () => { if (_chartDraw) _chartDraw(); }] },
  };

  try {
    plot = new uPlot(opts, data, wrap);
  } catch (err) {
    console.error('uPlot init error', err);
    plot = null; return;
  }

  if (onNav) attachInteractions(plot, onNav);

  // ── Tooltip ──────────────────────────────────────────────────────────────
  const tip = $("#tooltip");
  plot.over.addEventListener('mousemove', e => {
    if (!lastRenderData) return;
    const rect = plot.over.getBoundingClientRect();
    const x    = e.clientX - rect.left;
    const tArr = lastRenderData.data[0];
    if (!tArr?.length) { tip.style.display = 'none'; return; }
    // uPlot's .over covers only the plot area, so x is already measured from
    // the plot's left edge; posToVal maps that plot-relative x → time.
    const xVal = plot.posToVal(x, 'x');
    const idx  = nearestIndex(tArr, xVal);
    if (idx < 0) { tip.style.display = 'none'; return; }
    if (_hoverSync) _hoverSync(xVal);

    let html = `<div class="tt-time">${new Date(tArr[idx]*1000).toLocaleString()}</div>`;
    let base = 1;
    lastRenderData.selected.forEach((c, i) => {
      const stride = lastRenderData.bandByChannel?.[i] ? 3 : 1;
      const v      = lastRenderData.data[base]?.[idx] ?? null;
      html += `<div class="tt-row">
        <span class="tt-swatch" style="background:${COLORS[i%COLORS.length]}"></span>
        <span class="tt-name">${c.name.replace(/^ATLAS_/, '')}</span>
        <span class="tt-val">${v === null ? '-' : (Math.round(v*1e6)/1e6)}</span>
      </div>`;
      if (_tooltipExtra) html += _tooltipExtra(i, v);
      base += stride;
    });

    tip.innerHTML = html;
    tip.style.display = 'block';
    tip.style.left    = Math.min(window.innerWidth  - 310, e.clientX + 14) + 'px';
    tip.style.top     = Math.min(window.innerHeight - 150, e.clientY + 14) + 'px';
  });
  plot.over.addEventListener('mouseleave', () => {
    tip.style.display = 'none';
    if (_hoverSync) _hoverSync(null);
  });
}

// ── Chart update (called from app.js) ────────────────────────────────────────
export function chartUpdate(data, selected, onNav, perChannelAxes, bandByChannel) {
  lastRenderData = {
    data, selected: selected.slice(0, MAX_CHANS),
    bandByChannel, onNav, perChannelAxes,
  };

  if (!data[0]?.length) {
    destroyPlot();
    document.getElementById('chart').innerHTML =
      '<div style="color:var(--dim);padding:12px">no data in selected range</div>';
    return;
  }

  const needRebuild = !plot || plot.series.length !== data.length;
  if (needRebuild) {
    destroyPlot();
    initPlot(data, selected, onNav, perChannelAxes, bandByChannel);
    if (zoom && plot) plot.setScale('x', { min: zoom[0], max: zoom[1] });
  } else {
    try {
      plot.setData(data);
      plot.redraw();
    } catch (err) {
      console.error('uPlot setData error', err);
      destroyPlot();
      initPlot(data, selected, onNav, perChannelAxes, bandByChannel);
    }
  }
}

// Rebuild the chart when its container changes size (e.g. heatmap/score panel
// toggled, window resized) — uPlot uses fixed pixel dimensions, so without
// this a shrunken chartwrap makes the old-size canvas overlap the panels below.
let _resizeObserver = null;
export function watchChartResize() {
  const wrap = document.getElementById('chartwrap');
  if (!wrap || typeof ResizeObserver === 'undefined') return;
  if (_resizeObserver) _resizeObserver.disconnect();
  _resizeObserver = new ResizeObserver(() => {
    if (!plot || !lastRenderData) return;
    const w = Math.max(400, wrap.clientWidth);
    const h = Math.max(240, wrap.clientHeight - 20);
    if (plot.width === w && plot.height === h) return;
    destroyPlot();
    chartUpdate(
      lastRenderData.data, lastRenderData.selected,
      lastRenderData.onNav, lastRenderData.perChannelAxes,
      lastRenderData.bandByChannel
    );
  });
  _resizeObserver.observe(wrap);
}

// ── SPOT score panel ──────────────────────────────────────────────────────────
export function renderScorePanel(scoreData) {
  const wrap = document.getElementById('scorechart');
  if (scorePlot) { scorePlot.destroy(); scorePlot = null; }
  if (!scoreData?.t?.length) { wrap.innerHTML = ''; return; }

  const w = Math.max(300, document.getElementById('chartwrap').clientWidth);
  const t = Float64Array.from(scoreData.t);
  const s = Float64Array.from(scoreData.score);

  const opts = {
    width: w, height: 80,
    series: [
      {},
      { stroke: '#FF4444', fill: 'rgba(255,68,68,0.25)', width: 1,
        points: { show: false } },
    ],
    axes: [
      { show: false },
      { size: 40, values: (u, vs) => vs.map(v => v?.toFixed(2)) },
    ],
    scales: { x: { time: true }, y: { min: 0, max: 1 } },
    cursor: { show: false },
  };

  scorePlot = new uPlot(opts, [t, s], wrap);

  // Draw threshold line as a plugin-style overlay
  if (scoreData.threshold != null) {
    const thr = scoreData.threshold;
    const origDraw = scorePlot.redraw.bind(scorePlot);
    const drawThreshold = () => {
      const ctx = scorePlot.ctx;
      const y   = scorePlot.valToPos(thr, 'y', true);
      if (isNaN(y)) return;
      ctx.save();
      ctx.strokeStyle = '#FF0000';
      ctx.lineWidth   = 1;
      ctx.setLineDash([6, 3]);
      ctx.beginPath();
      ctx.moveTo(scorePlot.bbox.left, y);
      ctx.lineTo(scorePlot.bbox.left + scorePlot.bbox.width, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
    };
    // Patch the draw cycle
    const origOver = scorePlot.over;
    drawThreshold();
    scorePlot.hooks.draw = (scorePlot.hooks.draw || []).concat([drawThreshold]);
    scorePlot.redraw();
  }
}

// ── Legend ────────────────────────────────────────────────────────────────────
export function buildLegend(selected) {
  const el = $('#legend');
  if (!selected?.length) {
    el.textContent = 'Select channels + date range, then click Go';
    return;
  }
  el.innerHTML = '';
  selected.slice(0, MAX_CHANS).forEach((c, i) => {
    const div = document.createElement('div'); div.className = 'legend-item';
    const sw  = document.createElement('span');
    sw.className = 'legend-swatch';
    sw.style.background = COLORS[i % COLORS.length];
    const name = document.createElement('div');
    name.textContent = c.name.replace(/^ATLAS_/, '');
    div.appendChild(sw); div.appendChild(name);
    el.appendChild(div);
  });
}

// ── Pan / zoom interactions ───────────────────────────────────────────────────
export function attachInteractions(u, onNav) {
  u.over.addEventListener('wheel', e => {
    e.preventDefault();
    const rect   = u.over.getBoundingClientRect();
    const v0min  = u.scales.x.min ?? u.posToVal(0, 'x');
    const v0max  = u.scales.x.max ?? u.posToVal(rect.width, 'x');
    const tCur   = u.posToVal(e.clientX - rect.left, 'x');
    const factor = Math.exp(e.deltaY * 0.0015);
    onNav(tCur - (tCur - v0min) * factor, tCur + (v0max - tCur) * factor);
  }, { passive: false });

  u.over.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const startX  = e.clientX;
    const v0      = { min: u.scales.x.min ?? u.posToVal(0, 'x'),
                      max: u.scales.x.max ?? u.posToVal(u.over.clientWidth, 'x') };
    const secPerPx = (v0.max - v0.min) / u.over.clientWidth;
    const move = ev => onNav(
      v0.min + (startX - ev.clientX) * secPerPx,
      v0.max + (startX - ev.clientX) * secPerPx);
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup',   up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup',   up);
  }, true);
}
