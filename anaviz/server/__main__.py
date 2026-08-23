"""Entry point: python -m server <serve> [args...]"""
from __future__ import annotations

import sys


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else None

    if cmd == "serve":
        import uvicorn
        host, port = "127.0.0.1", 8000
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--host" and i + 1 < len(args): host = args[i + 1]
            if a == "--port" and i + 1 < len(args): port = int(args[i + 1])
        uvicorn.run("server.app:app", host=host, port=port, reload=True)
    else:
        print("Usage: python -m server serve [--host H] [--port P]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
