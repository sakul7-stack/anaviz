"""Entry point: python -m anaviz_datasource <ingest|download> [args...]

This package is fully independent of the anaviz project. It owns the HLT
data pipeline (download, ingest) and the /archive/* API server.
"""
from __future__ import annotations

import sys


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == "ingest":
        from anaviz_datasource.ingest_hlt import main as ingest_main
        sys.argv = ["ingest"] + sys.argv[2:]
        ingest_main()
    elif cmd == "download":
        from anaviz_datasource.download_hlt import main as download_main
        sys.argv = ["download"] + sys.argv[2:]
        download_main()
    else:
        print("Usage: python -m anaviz_datasource <ingest|download> [args...]",
              file=sys.stderr)
        print("  ingest    load HLT CSVs into the datasource DB", file=sys.stderr)
        print("  download  fetch the HLT CSVs from Zenodo", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
