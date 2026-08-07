export const $ = s => document.querySelector(s);

export const MAX_CHANS = 12;

export const COLORS = [
  "#0d6efd","#dc3545","#198754","#fd7e14",
  "#6f42c1","#0dcaf0","#e83e8c","#20c997",
  "#ffc107","#6610f2","#17a2b8","#d63384",
];

export const px = () => Math.max(300, $("#chartwrap").clientWidth - 20);

export function toLocalISO(d) {
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function parseLocalISO(s) {
  if (!s) return null;
  const d = new Date(s + (s.includes("T") ? "" : "T00:00"));
  return isNaN(d) ? null : d.getTime() / 1000;
}

export async function api(path, params, signal) {
  const u = new URL(path, location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach(x => u.searchParams.append(k, x));
    else u.searchParams.set(k, v);
  });
  const r = await fetch(u, signal ? { signal } : {});
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function nearestIndex(arr, x) {
  if (!arr || arr.length === 0) return -1;
  let lo = 0, hi = arr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < x) lo = mid; else hi = mid;
  }
  return Math.abs(arr[lo] - x) <= Math.abs(arr[hi] - x) ? lo : hi;
}

/**
 * Resample (tArr, vArr) onto shared x axis allT.
 * RAW mode: exact hits only (null elsewhere).
 * Default: linear interpolation; gaps outside data range are null.
 */
export function interpTo(allT, tArr, vArr, rawMode) {
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
  for (let i = 0; i < m; i++) {
    const x = allT[i];
    while (j + 1 < n && tArr[j + 1] <= x) j++;
    if (j < 0) {
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

/** Format seconds duration as "Xh Ym Zs". */
export function fmtDuration(sec) {
  if (sec < 60)   return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec/60)}m ${Math.round(sec%60)}s`;
  return `${Math.floor(sec/3600)}h ${Math.floor((sec%3600)/60)}m`;
}
