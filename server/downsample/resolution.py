"""Resolution selection: the 'map tiles for time' logic."""
from __future__ import annotations

from dataclasses import dataclass

LEVELS = [
    ("raw", "eventhistory", 0),
    ("1m",  "eh_1m",        60),
    ("5m",  "eh_5m",        300),
    ("1h",  "eh_1h",        3600),
    ("1d",  "eh_1d",        86400),
]

RAW_ROW_CAP = 200_000


@dataclass
class Resolution:
    name: str
    table: str
    bucket_s: int
    is_raw: bool


def select_resolution(span_s: float, px: int) -> Resolution:
    """Coarsest level with bucket duration <= span/px (>=1 bucket per pixel)."""
    target_bucket = span_s / max(px, 1)
    chosen = LEVELS[0]
    for level in LEVELS:
        if level[2] <= target_bucket:
            chosen = level
    name, table, bucket = chosen
    # Force at least 1m rollup when span > 2 hours (raw would be too many rows)
    if name == "raw" and span_s > 2 * 3600:
        name, table, bucket = LEVELS[1]
    return Resolution(name=name, table=table, bucket_s=bucket, is_raw=(name == "raw"))
