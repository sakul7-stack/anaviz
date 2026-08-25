/**
 * stats.js — Per-entity summary statistics table for the visible range.
 *
 * Numbers are computed from the observed (non-interpolated) values that
 * were aligned onto the shared time axis — the same source the histograms
 * use — plus gap counts from the query response.
 */
import { COLORS, MAX_SERIES } from "./utils.js";

// Public API

/**
 * Render the summary table for the currently selected entities.
 * @param {Array} selected
 * @param {Object|null} renderData - chart.js lastRenderData
 */
export function updateStatsTable(selected, renderData) {
  const el = document.getElementById("statstable");
  if (!el) return;

  const entities = (selected || []).slice(0, MAX_SERIES);
  if (!entities.length || !renderData?.entityMeta) {
    clearStatsTable();
    return;
  }

  const rows = entities.map((entity, i) => {
    const meta = renderData.entityMeta[i] || {};
    const values = (meta.observed || []).filter((v) => v != null && isFinite(v));
    const gaps = meta.gaps?.length ?? 0;
    return { entity, values, gaps, color: COLORS[i % COLORS.length] };
  });

  const esc = (s) =>
    String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const html = rows.map(({ entity, values, gaps, color }) => {
    const s = summarize(values);
    return `
      <tr>
        <td><span class="legend-swatch" style="background:${color}"></span>${esc(entity.label || entity.name)}</td>
        <td>${fmt(s.last)}</td>
        <td>${fmt(s.min)}</td>
        <td>${fmt(s.max)}</td>
        <td>${fmt(s.mean)}</td>
        <td>${fmt(s.std)}</td>
        <td>${values.length}</td>
        <td>${gaps || "-"}</td>
      </tr>`;
  }).join("");

  el.innerHTML = `
    <table class="stats-table">
      <thead>
        <tr>
          <th>Entity</th><th>Last</th><th>Min</th><th>Max</th>
          <th>Mean</th><th>σ</th><th>n</th><th>Gaps</th>
        </tr>
      </thead>
      <tbody>${html}</tbody>
    </table>`;
}

/** Clear the table. */
export function clearStatsTable() {
  const el = document.getElementById("statstable");
  if (el) el.innerHTML = "";
}

// Helpers

function summarize(values) {
  if (!values.length) {
    return { last: null, min: null, max: null, mean: null, std: null };
  }

  let sum = 0;
  let sumSq = 0;
  let min = Infinity;
  let max = -Infinity;

  for (const v of values) {
    sum += v;
    sumSq += v * v;
    if (v < min) min = v;
    if (v > max) max = v;
  }

  const mean = sum / values.length;
  const variance = Math.max(0, sumSq / values.length - mean * mean);

  return {
    last: values[values.length - 1],
    min,
    max,
    mean,
    std: Math.sqrt(variance),
  };
}

function fmt(v) {
  if (v == null || !isFinite(v)) return "-";
  const a = Math.abs(v);
  if (a >= 10000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(3).replace(/\.?0+$/, "");
  return String(Math.round(v * 1e4) / 1e4);
}
