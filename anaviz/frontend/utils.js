/**
 * utils.js — Shared helpers, API calls, data transformation.
 */

// DOM + constants

export const $ = (selector) => document.querySelector(selector);

export const MAX_SERIES = 12;

export const COLORS = [
  "#0d6efd", "#dc3545", "#198754", "#fd7e14",
  "#6f42c1", "#0dcaf0", "#e83e8c", "#20c997",
  "#ffc107", "#6610f2", "#17a2b8", "#d63384",
];

/** Available chart width in pixels. */
export function chartPixelWidth() {
  const wrap = $("#chartwrap");
  return Math.max(300, wrap.clientWidth - 20);
}

// Date helpers

/** Format a Date as a datetime-local input value. */
export function toLocalISO(date) {
  const pad = (n) => String(n).padStart(2, "0");
  const y = date.getFullYear();
  const m = pad(date.getMonth() + 1);
  const d = pad(date.getDate());
  const hh = pad(date.getHours());
  const mm = pad(date.getMinutes());
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

/** Parse a datetime-local input value to a Unix timestamp. */
export function parseLocalISO(str) {
  if (!str) return null;
  const date = new Date(str + (str.includes("T") ? "" : "T00:00"));
  return isNaN(date) ? null : date.getTime() / 1000;
}

// API helpers

/**
 * Make a GET request to the API.
 * @param {string} path  - API endpoint (e.g. "/api/datasets")
 * @param {Object} params - Query parameters
 * @param {AbortSignal} signal - Optional abort signal
 * @param {string} method - HTTP method override
 */
export async function api(path, params = {}, signal, method) {
  const url = new URL(path, location.origin);

  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      value.forEach((v) => url.searchParams.append(key, v));
    } else {
      url.searchParams.set(key, value);
    }
  }

  const options = {};
  if (method) options.method = method;
  if (signal) options.signal = signal;

  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.status === 204 ? null : response.json();
}

/**
 * Make a POST request to the API.
 * @param {string} path  - API endpoint
 * @param {Object} body  - JSON body
 * @param {AbortSignal} signal - Optional abort signal
 */
export async function apiPost(path, body, signal) {
  const url = new URL(path, location.origin);

  const options = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  if (signal) options.signal = signal;

  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

// Response adapters

/**
 * Convert the canonical /api/query response into the format
 * the chart renderer expects.
 */
export function seriesResponseToRenderer(response) {
  const downsampling = response?.provenance?.downsampling || "M4";

  return (response?.series || []).map((item) => {
    const points = item.points || {};
    const metrics = item.metrics || {};

    const series = {
      t: points.t || [],
      avg: points.value || [],
      quality_flag: points.quality || [],
      sample_count: points.sample_count || [],
    };

    // Copy band data if present (min/max/first/last with timestamps)
    const bandKeys = [
      "min", "max", "first", "last",
      "min_t", "max_t", "first_t", "last_t",
    ];
    for (const key of bandKeys) {
      if (points[key] != null) {
        series[key] = points[key];
      }
    }

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

/**
 * Convert the canonical /api/matrix response into heatmap format.
 */
export function matrixResponseToHeatmap(response) {
  return {
    t: response?.t || [],
    entity_ids: response?.entity_ids || [],
    scores: response?.values || [],
    score_kind: response?.value_semantics,
    missing_value: response?.missing_value ?? null,
  };
}

// Time axis + interpolation

/**
 * Build a shared time axis from all series results.
 * Inserts synthetic markers inside declared gaps so lines break there.
 */
export function buildSharedTimeAxis(results) {
  const timestamps = [];

  for (const result of results || []) {
    if (!result || result.error) continue;

    // Add all data timestamps
    timestamps.push(...(result.series?.t || []));

    // Add gap midpoints (so lines visually break across gaps)
    for (const gap of result.gap_intervals || []) {
      const start = Number(gap?.[0]);
      const end = Number(gap?.[1]);
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        timestamps.push(start + (end - start) / 2);
      }
    }
  }

  // Deduplicate and sort
  return [...new Set(timestamps)].sort((a, b) => a - b);
}

/**
 * Binary search for the index of the nearest value to x in a sorted array.
 */
export function nearestIndex(array, x) {
  if (!array?.length) return -1;

  let low = 0;
  let high = array.length - 1;

  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (array[mid] < x) low = mid;
    else high = mid;
  }

  return Math.abs(array[low] - x) <= Math.abs(array[high] - x) ? low : high;
}

/**
 * Resample (timeArray, valueArray) onto the shared time axis (allTimes).
 *
 * @param {string} mode - Interpolation mode:
 *   "linear" (default) - straight line between points
 *   "step"             - horizontal lines (deadband / zero-order hold)
 *   "none"             - dots only (null between points)
 *   true                - alias for "none" (raw mode)
 */
export function interpTo(allTimes, timeArray, valueArray, mode = "linear", gaps = []) {
  const count = allTimes.length;
  const result = new Array(count);

  // Normalize mode
  const interpMode = mode === true ? "none" : mode;

  // Raw / none mode: exact matches only
  if (interpMode === "none") {
    const lookup = {};
    for (let j = 0; j < timeArray.length; j++) {
      lookup[timeArray[j]] = valueArray[j];
    }
    for (let i = 0; i < count; i++) {
      result[i] = lookup[allTimes[i]] ?? null;
    }
    return result;
  }

  if (timeArray.length === 0) return result;

  let dataIndex = -1;
  let gapIndex = 0;

  for (let i = 0; i < count; i++) {
    const x = allTimes[i];

    // Advance gap pointer
    while (gapIndex < gaps.length && x >= gaps[gapIndex][1]) {
      gapIndex++;
    }

    // Check if we're inside a gap
    const inGap = gapIndex < gaps.length
      && x > gaps[gapIndex][0]
      && x < gaps[gapIndex][1];

    // Advance data pointer
    while (dataIndex + 1 < timeArray.length && timeArray[dataIndex + 1] <= x) {
      dataIndex++;
    }

    if (inGap || dataIndex < 0) {
      result[i] = null;
    } else if (dataIndex + 1 < timeArray.length) {
      const t0 = timeArray[dataIndex];
      const t1 = timeArray[dataIndex + 1];
      const dt = t1 - t0;

      if (interpMode === "step") {
        // Step / deadband: hold previous value until next point
        result[i] = valueArray[dataIndex];
      } else {
        // Linear interpolation
        const fraction = dt === 0 ? 0 : (x - t0) / dt;
        result[i] = valueArray[dataIndex]
          + (valueArray[dataIndex + 1] - valueArray[dataIndex]) * fraction;
      }
    } else {
      // Past the last data point: hold last value
      result[i] = valueArray[dataIndex];
    }
  }

  return result;
}

/**
 * Exact-match alignment (no interpolation). Used for quality flags, sample counts.
 */
export function alignExact(allTimes, timeArray, valueArray) {
  return interpTo(allTimes, timeArray, valueArray, "none");
}
