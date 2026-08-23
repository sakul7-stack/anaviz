/**
 * chart.js — uPlot chart lifecycle, hover tooltip, legend, pan/zoom.
 *
 * This module owns the main time-series chart. It manages:
 *  - Creating and destroying the uPlot instance
 *  - Hover tooltip with per-entity values
 *  - Pan (drag) and zoom (scroll wheel)
 *  - Cursor sync with the heatmap
 *  - Auto-resize on window/container changes
 */
import { $, COLORS, MAX_SERIES, nearestIndex } from "./utils.js";

// ── State ────────────────────────────────────────────────────────────────

export let plot = null;           // current uPlot instance
export let lastRenderData = null; // data from the last chartUpdate call

let zoom = null;           // [start, end] when zoomed, null when at full extent
let syncCursorTime = null; // shared time cursor (set from heatmap hover)
let hoverSyncCallback = null;  // chart hover → heatmap callback
let chartDrawCallback = null;  // called after every chart redraw
let tooltipEnricher = null;    // extra tooltip HTML per entity

// ── Public API ───────────────────────────────────────────────────────────

export function destroyPlot() {
  if (plot) {
    plot.destroy();
    plot = null;
  }
}

export function setZoom(start, end) {
  zoom = [start, end];
}

export function clearZoom() {
  zoom = null;
}

/** Draw a vertical cursor line at the given time (or clear it). */
export function setChartCursor(time) {
  syncCursorTime = time;
  if (plot) plot.redraw();
}

/** Register callback: chart hover → heatmap sync. */
export function setHoverSync(fn) {
  hoverSyncCallback = fn;
}

/** Register callback: called after every chart redraw. */
export function setChartDrawSync(fn) {
  chartDrawCallback = fn;
}

/** Register callback: extra tooltip HTML per entity. */
export function setTooltipEnricher(fn) {
  tooltipEnricher = fn;
}

/**
 * Get the current plot area geometry in CSS pixels.
 * Used by heatmap to align its columns with the chart.
 */
export function getPlotGeometry() {
  const wrap = document.getElementById("chart");
  const cssWidth = plot ? plot.width : Math.max(400, wrap?.clientWidth || 400);

  if (!plot || !plot.bbox?.width) {
    return { cssWidth, gutter: 0, plotWidth: cssWidth, hasPlot: !!plot };
  }

  const scale = (plot.ctx.canvas.width || 0) / (plot.width || 1) || 1;
  return {
    cssWidth,
    gutter: plot.bbox.left / scale,
    plotWidth: plot.bbox.width / scale,
    hasPlot: true,
  };
}

/** Convert a unix timestamp to a CSS x-position in the plot area. */
export function xForTime(time, range = null) {
  if (plot) {
    const pos = plot.valToPos(time, "x", false);
    if (isFinite(pos)) return pos;
  }
  if (range?.t1 > range.t0) {
    const geometry = getPlotGeometry();
    return ((time - range.t0) / (range.t1 - range.t0)) * geometry.plotWidth;
  }
  return 0;
}

/** Get per-entity values at the nearest data point to the given time. */
export function getValuesAtTime(time) {
  const data = lastRenderData;
  if (!data?.data?.[0]?.length) return null;

  const index = nearestIndex(data.data[0], time);
  if (index < 0) return null;

  const values = [];
  let base = 1;
  data.selected.forEach((entity, i) => {
    values.push(data.data[base]?.[index] ?? null);
    base += data.bandByEntity?.[i] ? 3 : 1;
  });

  return { t: data.data[0][index], values, selected: data.selected };
}

// ── Chart init ───────────────────────────────────────────────────────────

export function initPlot(data, selected, onNav, perEntityAxes, bandByEntity, interpMode) {
  const wrap = document.getElementById("chart");
  wrap.innerHTML = "";

  const width = Math.max(400, document.getElementById("chartwrap").clientWidth);
  const height = Math.max(240, document.getElementById("chartwrap").clientHeight - 20);

  const xScale = zoom
    ? { min: zoom[0], max: zoom[1], time: true }
    : { time: true };

  const series = [{}]; // index 0 is the x-axis placeholder
  const bands = [];
  let axes, scales;

  // Helper: build series config for a visible entity
  function makeSeries(entity, scaleKey) {
    const cfg = {
      label: entity.label || entity.name,
      stroke: COLORS[series.length % COLORS.length],
      width: 1.5,
    };
    if (scaleKey) cfg.scale = scaleKey;
    if (interpMode === "step") {
      cfg.paths = uPlot.paths.stepped({ align: 1 });
    } else if (interpMode === "none") {
      cfg.stroke = "rgba(0,0,0,0)";
      cfg.lineWidth = 0;
      cfg.points = { show: true, size: 3 };
    }
    return cfg;
  }

  // ── Build series + axes for each entity ──────────────────────────────

  if (perEntityAxes) {
    // Each entity gets its own y-axis
    scales = { x: xScale };
    axes = [{ space: 50 }];

    selected.slice(0, MAX_SERIES).forEach((entity, i) => {
      const scaleKey = i === 0 ? "y" : `y${i}`;
      scales[scaleKey] = {};
      const color = COLORS[series.length % COLORS.length];
      const baseIndex = series.length;

      series.push({
        ...makeSeries(entity, scaleKey),
        stroke: interpMode === "none" ? "rgba(0,0,0,0)" : color,
      });

      if (bandByEntity?.[i]) {
        series.push({ stroke: "rgba(0,0,0,0)", points: { show: false }, scale: scaleKey });
        series.push({ stroke: "rgba(0,0,0,0)", points: { show: false }, scale: scaleKey });
        bands.push({ series: [baseIndex + 2, baseIndex + 1], fill: color + "26" });
      }

      axes.push({
        scale: scaleKey,
        side: i % 2 === 0 ? 3 : 1,
        grid: { show: i % 2 === 0 },
        size: 70,
        stroke: color,
      });
    });
  } else {
    // All entities share one y-axis
    selected.slice(0, MAX_SERIES).forEach((entity, i) => {
      const color = COLORS[series.length % COLORS.length];
      const baseIndex = series.length;

      series.push(makeSeries(entity));

      if (bandByEntity?.[i]) {
        series.push({ stroke: "rgba(0,0,0,0)", points: { show: false } });
        series.push({ stroke: "rgba(0,0,0,0)", points: { show: false } });
        bands.push({ series: [baseIndex + 2, baseIndex + 1], fill: color + "26" });
      }
    });

    axes = [
      { space: 50 },
      { grid: { show: true }, size: 70 },
    ];
    scales = { x: xScale };
  }

  // ── Draw hook: sync cursor + trigger redraw callback ─────────────────

  const drawHook = (u) => {
    if (syncCursorTime == null) return;

    const x = Math.round(u.valToPos(syncCursorTime, "x", true));
    if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) return;

    const ctx = u.ctx;
    ctx.save();
    ctx.strokeStyle = "rgba(0,0,0,0.35)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, u.bbox.top);
    ctx.lineTo(x, u.bbox.top + u.bbox.height);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  };

  // ── Create uPlot ────────────────────────────────────────────────────

  try {
    plot = new uPlot(
      {
        width,
        height,
        series,
        axes,
        scales,
        bands,
        cursor: { y: true, x: false },
        dblclick: false,
        hooks: {
          draw: [drawHook, () => chartDrawCallback?.()],
        },
      },
      data,
      wrap
    );
  } catch (err) {
    console.error("uPlot init error", err);
    return;
  }

  if (onNav) attachInteractions(plot, onNav);
  setupTooltip(plot);
}

// ── Tooltip ──────────────────────────────────────────────────────────────

function setupTooltip(chart) {
  const tip = $("#tooltip");

  chart.over.addEventListener("mousemove", (e) => {
    if (!lastRenderData) return;

    const rect = chart.over.getBoundingClientRect();
    const xValue = chart.posToVal(e.clientX - rect.left, "x");
    const index = nearestIndex(lastRenderData.data[0], xValue);
    if (index < 0) {
      tip.style.display = "none";
      return;
    }

    // Sync heatmap cursor
    hoverSyncCallback?.(xValue);

    // Build tooltip HTML
    const timeStr = new Date(lastRenderData.data[0][index] * 1000).toLocaleString();
    let html = `<div class="tt-time">${timeStr}</div>`;

    let base = 1;
    lastRenderData.selected.forEach((entity, i) => {
      const value = lastRenderData.data[base]?.[index] ?? null;
      const meta = lastRenderData.entityMeta?.[i] || {};
      const wasObserved = meta.observed?.[index] != null;
      const quality = meta.quality?.[index];
      const count = meta.sampleCount?.[index];

      const evidence = wasObserved
        ? `observed · n=${count ?? 1}`
        : "interpolated/rollup";

      const color = COLORS[i % COLORS.length];
      const displayValue = value == null ? "-" : Math.round(value * 1e6) / 1e6;

      html += `
        <div class="tt-row">
          <span class="tt-swatch" style="background:${color}"></span>
          <span class="tt-name">${entity.label || entity.name}</span>
          <span class="tt-val">${displayValue}</span>
        </div>
        <div class="tt-meta">${evidence}; quality=${quality ?? "unknown"}</div>`;

      // Extra tooltip info (e.g. histogram bin)
      if (tooltipEnricher) {
        html += tooltipEnricher(i, value);
      }

      base += lastRenderData.bandByEntity?.[i] ? 3 : 1;
    });

    tip.innerHTML = html;
    tip.style.display = "block";
    tip.style.left = Math.min(innerWidth - 310, e.clientX + 14) + "px";
    tip.style.top = Math.min(innerHeight - 150, e.clientY + 14) + "px";
  });

  chart.over.addEventListener("mouseleave", () => {
    tip.style.display = "none";
    hoverSyncCallback?.(null);
  });
}

// ── Chart update ─────────────────────────────────────────────────────────

/**
 * Update the chart with new data. Rebuilds if the series count changed,
 * otherwise just updates the data in place.
 */
export function chartUpdate(data, selected, onNav, perEntityAxes, bandByEntity, entityMeta = [], interpMode) {
  const prevInterpMode = lastRenderData?.interpMode;

  lastRenderData = {
    data,
    selected: selected.slice(0, MAX_SERIES),
    bandByEntity,
    entityMeta,
    onNav,
    perEntityAxes,
    interpMode,
  };

  if (!data[0]?.length) {
    destroyPlot();
    document.getElementById("chart").innerHTML =
      '<div style="color:var(--dim);padding:12px">no data in selected range</div>';
    return;
  }

  const needsRebuild = !plot || plot.series.length !== data.length || interpMode !== prevInterpMode;

  if (needsRebuild) {
    destroyPlot();
    initPlot(data, selected, onNav, perEntityAxes, bandByEntity, interpMode);
    if (zoom && plot) {
      plot.setScale("x", { min: zoom[0], max: zoom[1] });
    }
  } else {
    try {
      plot.setData(data);
      plot.redraw();
    } catch {
      destroyPlot();
      initPlot(data, selected, onNav, perEntityAxes, bandByEntity, interpMode);
    }
  }
}

// ── Legend ────────────────────────────────────────────────────────────────

export function buildLegend(selected) {
  const el = $("#legend");

  if (!selected?.length) {
    el.textContent = "Select entities + date range, then click Go";
    return;
  }

  el.innerHTML = "";
  selected.slice(0, MAX_SERIES).forEach((entity, i) => {
    const div = document.createElement("div");
    div.className = "legend-item";

    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = COLORS[i % COLORS.length];

    const name = document.createElement("div");
    name.textContent = entity.label || entity.name;

    div.append(swatch, name);
    el.appendChild(div);
  });
}

// ── Auto-resize ──────────────────────────────────────────────────────────

let resizeObserver = null;

export function watchChartResize() {
  const wrap = document.getElementById("chartwrap");
  if (!wrap || typeof ResizeObserver === "undefined") return;

  if (resizeObserver) resizeObserver.disconnect();

  resizeObserver = new ResizeObserver(() => {
    if (!plot || !lastRenderData) return;

    const w = Math.max(400, wrap.clientWidth);
    const h = Math.max(240, wrap.clientHeight - 20);

    // Skip if size hasn't changed
    if (plot.width === w && plot.height === h) return;

    destroyPlot();
    chartUpdate(
      lastRenderData.data,
      lastRenderData.selected,
      lastRenderData.onNav,
      lastRenderData.perEntityAxes,
      lastRenderData.bandByEntity,
      lastRenderData.entityMeta,
      lastRenderData.interpMode
    );
  });

  resizeObserver.observe(wrap);
}

// ── Pan / zoom interactions ──────────────────────────────────────────────

function attachInteractions(chart, onNav) {
  // Scroll wheel → zoom in/out
  chart.over.addEventListener("wheel", (e) => {
    e.preventDefault();

    const rect = chart.over.getBoundingClientRect();
    const viewMin = chart.scales.x.min ?? chart.posToVal(0, "x");
    const viewMax = chart.scales.x.max ?? chart.posToVal(rect.width, "x");
    const cursorTime = chart.posToVal(e.clientX - rect.left, "x");
    const factor = Math.exp(e.deltaY * 0.0015);

    const newMin = cursorTime - (cursorTime - viewMin) * factor;
    const newMax = cursorTime + (viewMax - cursorTime) * factor;
    onNav(newMin, newMax);
  }, { passive: false });

  // Mouse drag → pan
  chart.over.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopImmediatePropagation();

    const startX = e.clientX;
    const viewMin = chart.scales.x.min ?? chart.posToVal(0, "x");
    const viewMax = chart.scales.x.max ?? chart.posToVal(chart.over.clientWidth, "x");
    const secondsPerPixel = (viewMax - viewMin) / chart.over.clientWidth;

    const onMouseMove = (ev) => {
      const offset = (startX - ev.clientX) * secondsPerPixel;
      onNav(viewMin + offset, viewMax + offset);
    };

    const onMouseUp = () => {
      removeEventListener("mousemove", onMouseMove);
      removeEventListener("mouseup", onMouseUp);
    };

    addEventListener("mousemove", onMouseMove);
    addEventListener("mouseup", onMouseUp);
  }, true);
}
