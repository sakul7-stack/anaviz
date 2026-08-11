export const $ = s => document.querySelector(s);

export const MAX_SERIES = 12;

export const COLORS = [
  "#0d6efd","#dc3545","#198754","#fd7e14",
  "#6f42c1","#0dcaf0","#e83e8c","#20c997",
  "#ffc107","#6610f2","#17a2b8","#d63384",
];

export const px = () => Math.max(300, $("#chartwrap").clientWidth - 20);

export function toLocalISO(d) {
  const pad = n => String(n).padStart(2, "0");
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return `${date}T${time}`;
}

export function parseLocalISO(s) {
  if (!s) return null;
  const d = new Date(s + (s.includes("T") ? "" : "T00:00"));
  return isNaN(d) ? null : d.getTime() / 1000;
}

export async function api(path, params, signal, method) {
  const u = new URL(path, location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach(x => u.searchParams.append(k, x));
    else u.searchParams.set(k, v);
  });
  const r = await fetch(u, {
    ...(method ? { method } : {}),
    ...(signal ? { signal } : {}),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.status === 204 ? null : r.json();
}

export async function apiPost(path, body, signal) {
  const r = await fetch(new URL(path, location.origin), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

/**
 * Adapt the canonical API response to the established renderer input. This
 * compatibility seam keeps chart modules independent of transport/storage
 * details while they migrate to generic entity terminology.
 */
export function seriesResponseToRenderer(response) {
  const downsampling = response?.provenance?.downsampling || 'M4';
  return (response?.series || []).map(item => {
    const points = item.points || {};
    const series = {
      t: points.t || [],
      avg: points.value || [],
      quality_flag: points.quality || [],
      sample_count: points.sample_count || [],
    };
    ['min', 'max', 'first', 'last', 'min_t', 'max_t', 'first_t', 'last_t']
      .forEach(key => {
        if (points[key] !== null && points[key] !== undefined) {
          series[key] = points[key];
        }
      });
    const metrics = item.metrics || {};
    return {
      entity_id: item.entity_id,
      error: item.error,
      resolution: item.resolution,
      algo: downsampling,
      rows_raw: metrics.rows_source || 0,
      rows_scanned: metrics.rows_scanned || 0,
      rows_returned: metrics.rows_returned || series.t.length,
      query_ms: metrics.query_ms || 0,
      archive_ms: metrics.source_ms || 0,
      ds_ms: metrics.downsample_ms || 0,
      truncated: Boolean(metrics.truncated),
      expected_step_seconds: item.expected_step_seconds,
      gap_intervals: item.gaps || [],
      gap_count: (item.gaps || []).length,
      quality_summary: item.quality_summary || {},
      data_fidelity: item.fidelity || {},
      series,
    };
  });
}

export function matrixResponseToHeatmap(response) {
  return {
    t: response?.t || [],
    entity_ids: response?.entity_ids || [],
    scores: response?.values || [],
    score_kind: response?.value_semantics,
    missing_value: response?.missing_value ?? null,
    data_fidelity: response?.fidelity || {},
  };
}

/**
 * Build the renderer x-axis from observed timestamps plus one synthetic axis
 * marker inside each declared source gap. The marker has no associated value;
 * it exists only so line renderers visibly break rather than connecting two
 * observed endpoints across missing coverage.
 */
export function buildSharedTimeAxis(results) {
  const timestamps = [];
  (results || []).forEach(result => {
    if (!result || result.error) return;
    timestamps.push(...(result.series?.t || []));
    (result.gap_intervals || []).forEach(gap => {
      const start = Number(gap?.[0]);
      const end = Number(gap?.[1]);
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        timestamps.push(start + (end - start) / 2);
      }
    });
  });
  return [...new Set(timestamps)].sort((a, b) => a - b);
}

export function nearestIndex(arr, x) {
  if (!arr || arr.length === 0) return -1;

  let lo = 0;
  let hi = arr.length - 1;

  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < x) lo = mid;
    else hi = mid;
  }

  return Math.abs(arr[lo] - x) <= Math.abs(arr[hi] - x) ? lo : hi;
}

/**
 * Resample (tArr, vArr) onto shared x axis allT.
 * RAW mode: exact hits only (null elsewhere).
 * Default: linear interpolation only between observed points that are not
 * separated by a declared source gap. Gaps are [start, end] unix seconds.
 */
export function interpTo(allT, tArr, vArr, rawMode, gaps = []) {
  const m = allT.length;
  const n = tArr.length;
  const vals = new Array(m);

  if (rawMode) {
    const map = {};
    for (let j = 0; j < n; j++) map[tArr[j]] = vArr[j];
    for (let i = 0; i < m; i++) {
      const v = map[allT[i]];
      vals[i] = v !== undefined ? v : null;
    }
    return vals;
  }

  if (n === 0) return vals;  // all undefined

  let j = -1;
  let gapIdx = 0;
  for (let i = 0; i < m; i++) {
    const x = allT[i];
    while (gapIdx < gaps.length && x >= gaps[gapIdx][1]) gapIdx++;
    const inGap = gapIdx < gaps.length &&
      x > gaps[gapIdx][0] && x < gaps[gapIdx][1];
    while (j + 1 < n && tArr[j + 1] <= x) j++;
    if (inGap) {
      vals[i] = null;
    } else if (j < 0) {
      vals[i] = null;
    } else if (j + 1 < n) {
      const t0 = tArr[j], t1 = tArr[j + 1];
      const dt = t1 - t0;
      const f  = dt === 0 ? 0 : (x - t0) / dt;
      vals[i]  = vArr[j] + (vArr[j + 1] - vArr[j]) * f;
    } else {
      vals[i] = x === tArr[j] ? vArr[j] : null;
    }
  }
  return vals;
}

/**
 * Align metadata to the shared x-axis without interpolation. This is used
 * for quality flags and sample counts, where fabricating a value is invalid.
 */
export function alignExact(allT, tArr, vArr) {
  return interpTo(allT, tArr, vArr, true);
}

/** Format seconds duration as "Xh Ym Zs". */
export function fmtDuration(sec) {
  if (sec < 60) {
    return `${Math.round(sec)}s`;
  }
  if (sec < 3600) {
    return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  }
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}
