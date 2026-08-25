/**
 * export.js — Client-side PNG/CSV export helpers shared by the panels.
 *
 * Everything is generated in-browser: figures are composed from the live
 * canvases (plus title/subtitle/axis labels) and serialized to PNG blobs,
 * tables are built as arrays of rows and encoded as CSV text.
 * Nothing touches the backend.
 */

// Download

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// CSV

export function saveCsv(rows, filename) {
  const text = rows.map((cells) => cells.map(csvField).join(",")).join("\r\n");
  downloadBlob(new Blob([text], { type: "text/csv;charset=utf-8" }), filename);
}

function csvField(value) {
  const s = value == null ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// PNG

/**
 * Compose a titled figure from one or more canvases and download it as PNG.
 *
 * Layout: optional title + subtitle centered on top, canvases stacked
 * vertically (each may carry its own caption), optional rotated y-axis
 * label on the left and x-axis label centered at the bottom. Everything
 * is flattened onto a white background.
 *
 * @param {Object} opts
 * @param {string} [opts.title]
 * @param {string} [opts.subtitle]
 * @param {string} [opts.xAxisLabel]
 * @param {string} [opts.yAxisLabel]
 * @param {Array<{canvas: HTMLCanvasElement, title?: string}>} opts.entries
 * @param {string} filename
 * @returns {boolean} false when there is nothing to export
 */
export function saveFigurePng(
  { title, subtitle, xAxisLabel, yAxisLabel, entries = [] },
  filename
) {
  if (!entries.length) return false;

  const scale = window.devicePixelRatio || 1;
  const pad = Math.round(14 * scale);
  const gap = 8 * scale;
  const TITLE_H = Math.round(22 * scale);
  const SUB_H = Math.round(17 * scale);
  const CAPTION_H = Math.round(16 * scale);
  const XLABEL_H = Math.round(18 * scale);
  const YLABEL_W = Math.round(18 * scale);

  let contentW = 0;
  let contentH = 0;
  for (const e of entries) {
    contentW = Math.max(contentW, e.canvas.width);
    contentH += (e.title ? CAPTION_H : 0) + e.canvas.height + gap;
  }
  contentH = Math.max(0, contentH - gap); // drop trailing gap

  const hasHeader = !!(title || subtitle);
  const width = pad * 2 + (yAxisLabel ? YLABEL_W : 0) + contentW;
  const height =
    pad +
    (title ? TITLE_H : 0) +
    (subtitle ? SUB_H : 0) +
    (hasHeader ? gap : 0) +
    contentH +
    (xAxisLabel ? gap + XLABEL_H : 0) +
    pad;

  const out = document.createElement("canvas");
  out.width = Math.max(1, width);
  out.height = Math.max(1, height);
  const ctx = out.getContext("2d");

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, out.width, out.height);

  const left = pad + (yAxisLabel ? YLABEL_W : 0);
  let y = pad;
  ctx.textBaseline = "top";

  if (title) {
    ctx.fillStyle = "#111111";
    ctx.font = `700 ${Math.round(15 * scale)}px system-ui`;
    ctx.textAlign = "center";
    ctx.fillText(title, left + contentW / 2, y);
    y += TITLE_H;
  }
  if (subtitle) {
    ctx.fillStyle = "#666666";
    ctx.font = `${Math.round(11 * scale)}px system-ui`;
    ctx.textAlign = "center";
    ctx.fillText(subtitle, left + contentW / 2, y);
    y += SUB_H;
  }
  if (hasHeader) y += gap;

  const contentTop = y;

  // Rotated y-axis label
  if (yAxisLabel) {
    ctx.save();
    ctx.translate(pad + Math.round(YLABEL_W / 2), contentTop + contentH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillStyle = "#444444";
    ctx.font = `600 ${Math.round(11 * scale)}px system-ui`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(yAxisLabel, 0, 0);
    ctx.restore();
  }

  // Canvases (+ optional per-canvas captions)
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  for (const e of entries) {
    if (e.title) {
      ctx.fillStyle = "#333333";
      ctx.font = `600 ${Math.round(11 * scale)}px system-ui`;
      ctx.fillText(e.title, left, y + 2 * scale);
      y += CAPTION_H;
    }
    ctx.drawImage(e.canvas, left, y);
    y += e.canvas.height + gap;
  }

  // X-axis label
  if (xAxisLabel) {
    ctx.fillStyle = "#444444";
    ctx.font = `600 ${Math.round(11 * scale)}px system-ui`;
    ctx.textAlign = "center";
    ctx.fillText(xAxisLabel, left + contentW / 2, y - gap + 3 * scale);
  }

  out.toBlob((blob) => blob && downloadBlob(blob, filename), "image/png");
  return true;
}
