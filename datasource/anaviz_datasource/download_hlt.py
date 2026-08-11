#!/usr/bin/env python3
"""
Download the ATLAS HLT p-beat CSV dataset from Zenodo record 7908064.

Files:
    hlt_train_set.csv   ~2.0 GB
    hlt_test_set.csv    ~995 MB
    hlt_val_set.csv     ~632 MB

Usage:
    anaviz-download                                     # all three (parallel)
    anaviz-download --only train                        # just train
    anaviz-download --check                             # verify sizes + md5

Downloads into data/hlt/. Parallel segmented download (each connection resumes
its own byte-range) — several times faster than single-stream on throttled
links. Re-running resumes unfinished segments and verifies md5 of finished
files. `--jobs N` sets the number of concurrent segments (default 8).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ZENODO_RECORD = "https://zenodo.org/records/7908064/files"

# filename -> (size_bytes, md5)  (md5 taken from the Zenodo record API)
KNOWN_FILES: dict[str, tuple[int, str]] = {
    "hlt_test_set.csv": (995_431_576, "dab4db8c208ca8cb5f27f5285dc6ff3d"),
    "hlt_val_set.csv":  (631_923_178, "be2d55a5eb4236ff88af96c3af309642"),
    "hlt_train_set.csv":(2_007_732_526, "9294a99cdd42bb0159678ede4d2c1f85"),
}

CHUNK = 1 << 20      # 1 MiB read buffer (md5)
SEGMENT = 64 << 20   # 64 MiB per parallel segment
MAX_ATTEMPTS = 5


def md5_of(path: Path, want: str) -> bool:
    """Streaming md5 check. Returns True on match, False on mismatch."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest() == want


def _segments(size: int) -> list[tuple[int, int]]:
    """Split [0, size) into 64 MiB byte ranges."""
    return [(start, min(start + SEGMENT, size))
            for start in range(0, size, SEGMENT)]


def _curl_retry(url: str, start: int, end: int, part: Path) -> bool:
    """Download one byte range into `part`, appending partial progress so a
    dropped connection never wastes the bytes already fetched. Returns True
    when the segment is complete."""
    while True:
        existing = part.stat().st_size if part.exists() else 0
        if existing >= end - start:
            return True
        try:
            args = ["curl", "-sS", "-L",
                    "--speed-limit", "1024", "--speed-time", "15",
                    "--max-time", "300",
                    "--range", f"{start + existing}-{end - 1}", url]
            r = subprocess.run(args, capture_output=True)
            if r.stdout:
                with open(part, "ab") as dst:
                    dst.write(r.stdout)
            if r.returncode == 0:
                continue  # loop checks completion
            print(f"    seg {start//SEGMENT}: curl {r.returncode}, "
                  f"{len(r.stdout):,} bytes appended", file=sys.stderr)
        except OSError as e:
            print(f"    seg {start//SEGMENT}: {e}", file=sys.stderr)
        time.sleep(1)


def download_parallel(url: str, dest: Path, size_want: int, jobs: int) -> bool:
    """Download a file in parallel byte-range segments, then stitch them."""
    segs = _segments(size_want)
    part_dir = dest.parent / f".{dest.name}.parts"
    part_dir.mkdir(exist_ok=True)
    parts = [part_dir / f"{i:04d}" for i in range(len(segs))]

    print(f"  {dest.name}: {len(segs)} segments × 64 MiB, {jobs} concurrent ...")
    done = False
    while not done:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_curl_retry, url, s, e, p): (s, p)
                       for (s, e), p in zip(segs, parts)}
            for fut in concurrent.futures.as_completed(futures):
                s, p = futures[fut]
                ok = fut.result()
                if ok:
                    print(f"    seg {s//SEGMENT}: done ({p.stat().st_size:,} bytes)")
                else:
                    print(f"    seg {s//SEGMENT}: FAILED", file=sys.stderr)
        done = all(not p.exists() or p.stat().st_size >= e - s
                   for (s, e), p in zip(segs, parts))

    # Stitch in order
    with open(dest, "wb") as out:
        for i, (s, e) in enumerate(segs):
            with open(parts[i], "rb") as p:
                shutil.copyfileobj(p, out, CHUNK)
    for p in parts:
        p.unlink()
    part_dir.rmdir()
    print(f"  {dest.name}: stitched ({dest.stat().st_size:,} bytes)")
    return dest.stat().st_size == size_want


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--only", default="", help="comma-separated subset of files")
    p.add_argument("--check", action="store_true", help="verify sizes, don't download")
    p.add_argument("--out", default="data/hlt", help="output directory")
    p.add_argument("--jobs", type=int, default=8, help="concurrent segments")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    names = args.only.split(",") if args.only else list(KNOWN_FILES)
    for name in names:
        if name not in KNOWN_FILES:
            raise SystemExit(f"ERROR: unknown file {name!r} (choose from {list(KNOWN_FILES)})")

    for name in names:
        size_want, md5_want = KNOWN_FILES[name]
        dest = out / name
        if args.check:
            if dest.exists() and dest.stat().st_size == size_want:
                ok = md5_of(dest, md5_want)
                print(f"{name}: {'OK (md5)' if ok else 'SIZE OK BUT HASH MISMATCH'}")
            else:
                print(f"{name}: INCOMPLETE "
                      f"({dest.stat().st_size if dest.exists() else 0:,} / {size_want:,} bytes)")
            continue
        if dest.exists() and dest.stat().st_size == size_want and md5_of(dest, md5_want):
            print(f"{name}: verified (md5 match), skipping")
            continue
        print(f"Downloading {name} ...")
        ok = download_parallel(f"{ZENODO_RECORD}/{name}", dest, size_want, args.jobs)
        if not ok:
            print(f"  {name}: size mismatch after download — re-run to finish")
            continue
        if md5_of(dest, md5_want):
            print(f"  {name}: md5 OK")
        else:
            print(f"  {name}: md5 MISMATCH — re-run to resume")


if __name__ == "__main__":
    main()
