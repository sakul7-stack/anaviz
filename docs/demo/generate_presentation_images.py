#!/usr/bin/env python3
"""Create four extra presentation diagrams for the Anaviz ODT.

This script only creates explanatory PNGs. It does not import or run Anaviz.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = Path(__file__).resolve().parent / "images"
OUT.mkdir(exist_ok=True)

COLORS = {
    "blue": "#cfe2ff",
    "blue_dark": "#1d5fd1",
    "green": "#d1e7dd",
    "green_dark": "#198754",
    "orange": "#ffe5cc",
    "orange_dark": "#d96b00",
    "purple": "#e2d9f3",
    "purple_dark": "#6f42c1",
    "red": "#f8d7da",
    "red_dark": "#c62828",
    "ink": "#18202a",
    "muted": "#5c6773",
}


def box(ax, x, y, w, h, text, face, edge, fontsize=13, weight="bold"):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.025",
        facecolor=face, edgecolor=edge, linewidth=2,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fontsize, fontweight=weight, color=COLORS["ink"],
            wrap=True)


def arrow(ax, x1, y1, x2, y2, label=None, color=COLORS["ink"]):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=18,
        linewidth=2, color=color,
    ))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.04, label,
                ha="center", va="center", fontsize=10, color=color,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.8))


def finish(ax, title, filename, xlim=(0, 1), ylim=(0, 1)):
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.axis("off")
    ax.set_title(title, fontsize=21, fontweight="bold", pad=18, color=COLORS["ink"])
    fig = ax.figure
    fig.tight_layout()
    fig.savefig(OUT / filename, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# 09 — API contract translation
fig, ax = plt.subplots(figsize=(15, 7.5))
box(ax, .03, .33, .22, .34, "SOURCE API\n\n/ archive / eventhistory\nelement_id\nt0 / t1\nt / value / quality_flag", COLORS["red"], COLORS["red_dark"], 12)
box(ax, .38, .33, .24, .34, "config.json\n\npath mapping\nparameter mapping\nshape: row/columnar\ntimestamp format", COLORS["orange"], COLORS["orange_dark"], 12)
box(ax, .75, .33, .22, .34, "CANONICAL API\n\nentity_ids\nrange\nmeasure_ids\npoints: t/value\ngaps + fidelity + metrics", COLORS["blue"], COLORS["blue_dark"], 12)
arrow(ax, .25, .50, .38, .50, "HTTP response")
arrow(ax, .62, .50, .75, .50, "adapter translates")
ax.text(.50, .15, "The frontend knows only the canonical contract — never the source database schema.",
        ha="center", fontsize=14, color=COLORS["green_dark"], fontweight="bold")
finish(ax, "New Diagram — Source API → Config → Canonical Contract", "09_api_contract_translation.png")

# 10 — cache layers
fig, ax = plt.subplots(figsize=(14, 8))
box(ax, .08, .64, .84, .22, "cache_series\nRAW requested samples\n(dataset_key, entity_id, measure_id, timestamp, value, quality)", COLORS["blue"], COLORS["blue_dark"], 14)
box(ax, .08, .36, .40, .17, "cache_coverage\nqueried / empty ranges\navoids repeated source probes", COLORS["green"], COLORS["green_dark"], 12)
box(ax, .52, .36, .40, .17, "cache_rollup\noptional bucket summaries\nfirst / min / max / last / avg / count", COLORS["purple"], COLORS["purple_dark"], 12)
box(ax, .20, .08, .60, .14, "dataset_key = dataset_id : config_fingerprint\nseparates every registered configuration", COLORS["orange"], COLORS["orange_dark"], 13)
arrow(ax, .50, .64, .28, .53, "coverage check", COLORS["green_dark"])
arrow(ax, .50, .64, .72, .53, "rollup built", COLORS["purple_dark"])
arrow(ax, .50, .36, .50, .22, "namespace", COLORS["orange_dark"])
finish(ax, "New Diagram — Three Generic Cache Layers", "10_cache_layers.png")

# 11 — frontend panels
fig, ax = plt.subplots(figsize=(14, 8))
box(ax, .05, .33, .25, .34, "Canonical\nSeriesQueryResponse\n\npoints\ngaps\nquality\nfidelity", COLORS["blue"], COLORS["blue_dark"], 13)
box(ax, .40, .62, .24, .20, "chart.js\nuPlot line chart\nzoom + pan + tooltip", COLORS["green"], COLORS["green_dark"], 12)
box(ax, .40, .38, .24, .20, "heatmap.js\nCanvas matrix\nrolling z-score", COLORS["purple"], COLORS["purple_dark"], 12)
box(ax, .40, .14, .24, .20, "histogram.js\nvalue distributions\nuses last chart data", COLORS["orange"], COLORS["orange_dark"], 12)
box(ax, .76, .33, .19, .34, "USER\n\nvisual evidence\n\nline + band\nheatmap\nhistogram\ntooltip", COLORS["red"], COLORS["red_dark"], 13)
arrow(ax, .30, .50, .40, .72, "render")
arrow(ax, .30, .50, .40, .48, "render")
arrow(ax, .30, .50, .40, .24, "render")
arrow(ax, .64, .72, .76, .52, "sync")
arrow(ax, .64, .48, .76, .50, "sync")
arrow(ax, .64, .24, .76, .48, "sync")
finish(ax, "New Diagram — One Canonical Response, Three Frontend Views", "11_frontend_panels.png")

# 12 — code to behavior
fig, ax = plt.subplots(figsize=(15, 7.5))
box(ax, .05, .42, .25, .24, "1. SIMPLE CODE\n\nrequest = SeriesQuery(...)\ncache.coverage(...)\ndownsample(...)", COLORS["orange"], COLORS["orange_dark"], 13)
box(ax, .38, .42, .25, .24, "2. BACKEND BEHAVIOR\n\nfetch missing ranges\nchoose tier\naggregate if dense\nreturn evidence", COLORS["blue"], COLORS["blue_dark"], 13)
box(ax, .71, .42, .24, .24, "3. PRESENTATION\n\nfast chart\nvisible gaps\nmin/max band\nquality tooltip", COLORS["green"], COLORS["green_dark"], 13)
arrow(ax, .30, .54, .38, .54, "controls")
arrow(ax, .63, .54, .71, .54, "visible result")
ax.text(.50, .18, "Each code snippet in the presentation should answer: what enters, what happens, what the audience sees.",
        ha="center", fontsize=14, color=COLORS["purple_dark"], fontweight="bold")
finish(ax, "New Diagram — Code → Backend Behavior → User Evidence", "12_code_to_behavior.png")

print("Generated 4 additional diagrams in", OUT)
