from __future__ import annotations

import argparse
import os

import uvicorn


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the codex-lb API server.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
