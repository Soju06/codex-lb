from __future__ import annotations

import argparse
import os

import uvicorn

from app.core.runtime_logging import build_log_config


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the codex-lb API server.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))
    parser.add_argument(
        "--timeout-keep-alive",
        type=int,
        default=int(os.getenv("UVICORN_TIMEOUT_KEEP_ALIVE", "7200")),
        help=(
            "Seconds to keep idle HTTP connections open. Codex CLI reuses local "
            "connections for large compact POSTs; short keepalive windows can leave the "
            "client writing to a stale socket before the request reaches the app."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

    os.environ["PORT"] = str(args.port)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
        timeout_keep_alive=args.timeout_keep_alive,
        log_config=build_log_config(),
    )


if __name__ == "__main__":
    main()
