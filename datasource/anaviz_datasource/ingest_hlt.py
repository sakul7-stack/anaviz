#!/usr/bin/env python3
"""
Ingest ATLAS HLT p-beat CSV dataset into TimescaleDB.

Dataset: Zenodo record 7908064 — https://zenodo.org/record/7908064

Files (download to data/hlt/):
    hlt_train_set.csv   ~2 GB
    hlt_test_set.csv    ~1 GB
    hlt_val_set.csv     ~632 MB

CSV format:
    Row index  = ISO timestamp with tz  (2018-04-18 18:39:55.001542+02:00)
    Columns    = full DCM node names    (DF_IS:HLT-24:tpu-rack-16...info)
    Values     = float Hz rates, NaN = missing

Usage:
    anaviz-ingest --data-dir data/hlt
    anaviz-ingest --data-dir data/hlt --files train
    anaviz-ingest --data-dir data/hlt --files all

The target DB is the DATASOURCE container (host port 5434), which owns the
real HLT data and serves it over /archive/* on :9000. The project container
only caches what it queries — never ingest into the project cache DB.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

DB_URL            = "postgresql://dcs:dcs@localhost:5434/dcs"
ELEMENT_ID_OFFSET = 900_000
COPY_CHUNK        = 4 << 20   # 4 MiB per COPY transport read
FLUSH_BYTES       = 64 << 20  # commit batch: one txn per 64 MiB of rows


def short_name(full_col: str, idx: int) -> str:
    """Derive a short readable channel name from the full DCM column.
    E.g. DF_IS:HLT-24:tpu-rack-16.DCM-NoTS:HLT-24:tpu-rack-16:pc-tdq-tpu-16002.info
    → HLT_DCM_sub24_rack16_node16002
    The subsystem id (HLT-24 / HLT-36) is included because the same rack/node
    numbers recur across subsystems, and full_name is unique in the DB."""
    try:
        rack = "?"
        node = str(idx)
        sub  = "?"
        for part in full_col.split(":"):
            if part.startswith("HLT-"):
                sub = part.split("-")[-1]
            if "rack-" in part:
                rack = part.split("rack-")[-1].split(".")[0]
        if "tpu-" in full_col:
            node = full_col.split("tpu-")[-1].replace(".info", "")
        return f"HLT_DCM_sub{sub}_rack{rack}_node{node}"
    except Exception:
        return f"HLT_channel_{idx}"


def ingest_file(path: Path, col_to_eid: dict[str, int],
                conn: psycopg.Connection) -> int:
    """Stream one CSV into eventhistory. Returns rows written."""
    cur     = conn.cursor()
    written = 0
    buf     = io.StringIO()

    # Load each COPY buffer into a temporary staging table, then merge it into
    # the uniquely keyed hypertable.  COPY itself has no ON CONFLICT clause,
    # so staging is what makes interrupted/repeated file loads idempotent.
    cur.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _hlt_ingest_stage ("
        "element_id INTEGER NOT NULL, ts TIMESTAMPTZ NOT NULL, "
        "value DOUBLE PRECISION NOT NULL, quality_flag SMALLINT NOT NULL) "
        "ON COMMIT DELETE ROWS")

    def flush_buffer() -> None:
        nonlocal buf
        if buf.tell() == 0:
            return
        buf.seek(0)
        with cur.copy(
            "COPY _hlt_ingest_stage (element_id, ts, value, quality_flag) "
            "FROM STDIN WITH (FORMAT csv)"
        ) as cp:
            while True:
                chunk = buf.read(COPY_CHUNK)
                if not chunk:
                    break
                cp.write(chunk.encode())
        cur.execute(
            "INSERT INTO eventhistory (element_id, ts, value, quality_flag) "
            "SELECT element_id, ts, value, quality_flag FROM _hlt_ingest_stage "
            "ON CONFLICT (element_id, ts) DO UPDATE SET "
            "value = EXCLUDED.value, quality_flag = EXCLUDED.quality_flag")
        cur.execute("TRUNCATE _hlt_ingest_stage")
        conn.commit()
        buf = io.StringIO()

    print(f"  Reading {path.name} ({path.stat().st_size/1e6:.0f} MB) ...")

    # Read in chunks to avoid loading 2 GB at once. The wide→long unpivot
    # runs in C (stack/dropna/to_csv); only non-NaN cells reach the buffer.
    chunksize = 2000
    reader = pd.read_csv(path, index_col=0, parse_dates=True, chunksize=chunksize)

    for chunk_n, df in enumerate(reader):
        df.index = pd.to_datetime(df.index, utc=True)

        usable = {c: e for c, e in col_to_eid.items() if c in df.columns}
        if usable:
            long = (df[list(usable)].rename(columns=usable)
                    .stack().dropna().reset_index())
            long.columns = ["ts", "element_id", "value"]
            long["quality_flag"] = 0
            long.to_csv(buf, index=False, header=False,
                        columns=["element_id", "ts", "value", "quality_flag"])
            written += len(long)

        # Flush COPY buffer periodically
        if buf.tell() >= FLUSH_BYTES:
            flush_buffer()

        print(f"    ... chunk {chunk_n}: {written:,} rows", flush=True)

    # Final flush
    flush_buffer()

    print(f"    {written:,} rows written from {path.name}        ")
    return written


def ingest(data_dir: str, files: str, db_url: str) -> None:
    data_path = Path(data_dir)

    file_map = {
        "train": data_path / "hlt_train_set.csv",
        "test":  data_path / "hlt_test_set.csv",
        "val":   data_path / "hlt_val_set.csv",
    }

    if files == "train":
        to_ingest = ["train"]
    elif files == "all":
        to_ingest = ["train", "test", "val"]
    else:  # default: train + test
        to_ingest = ["train", "test"]

    for key in to_ingest:
        if not file_map[key].exists():
            sys.exit(f"ERROR: {file_map[key]} not found.\n"
                     f"Download from https://zenodo.org/record/7908064")

    # Read column names from train header only
    print("[1/4] Reading column names from train CSV header ...")
    with open(file_map["train"]) as f:
        header = f.readline()
    columns = [c.strip() for c in header.split(",")[1:] if c.strip()]
    print(f"      {len(columns)} channels")

    # Connect
    print(f"[2/4] Connecting to TimescaleDB ...")
    try:
        conn = psycopg.connect(db_url, autocommit=False)
    except Exception as e:
        sys.exit(f"ERROR: {e}")
    cur = conn.cursor()
    # Bulk-load mode: don't fsync WAL on every commit (big crash window, but
    # the upsert makes re-running after a crash fully safe).
    cur.execute("SET synchronous_commit TO OFF")
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_eventhistory_element_ts "
            "ON eventhistory (element_id, ts)")
        conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        sys.exit(
            "ERROR: eventhistory already contains duplicate (element_id, ts) "
            "rows. Refusing to guess which source value is authoritative; "
            "repair or recreate the datasource volume, then re-run ingestion."
        )

    # hardware_mapping
    print(f"[3/4] Writing hardware_mapping ({len(columns)} channels) ...")
    col_to_eid: dict[str, int] = {}
    for i, col in enumerate(columns):
        eid = ELEMENT_ID_OFFSET + i
        col_to_eid[col] = eid
        cur.execute(
            "INSERT INTO hardware_mapping (element_id, full_name) "
            "VALUES (%s, %s) "
            "ON CONFLICT (element_id) DO UPDATE SET full_name=EXCLUDED.full_name",
            (eid, short_name(col, i)))
    conn.commit()
    print(f"      element_ids {ELEMENT_ID_OFFSET} – {ELEMENT_ID_OFFSET+len(columns)-1}")

    # COPY rows
    print(f"[4/4] Ingesting CSV files ...")
    total = 0
    for key in to_ingest:
        n = ingest_file(file_map[key], col_to_eid, conn)
        total += n

    cur.close()
    conn.close()

    print(f"\nDone! {total:,} rows ingested into the datasource DB.")
    print("\nNext — the datasource /archive/* API on :9000 now serves this data.")
    print("  Start the stack with:")
    print("  make db-up && make app-up   (or: sudo docker compose up -d --build)")
    print("  Then open http://localhost:8000")
    print("  (In the app's Data sources panel, register this API with a")
    print("   config.json pointing at http://datasource:9000; the project")
    print("   hydrates its cache on demand — nothing is precomputed.)")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Ingest ATLAS HLT p-beat CSV data into TimescaleDB.")
    p.add_argument("--data-dir", default="data/hlt")
    p.add_argument("--files",    default="train+test",
                   choices=["train", "train+test", "all"])
    p.add_argument("--db-url",   default=DB_URL)
    args = p.parse_args()
    ingest(args.data_dir, args.files, args.db_url)


if __name__ == "__main__":
    main()
