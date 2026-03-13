from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from app.core.logging import build_log_config, configure_logging

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the codex-lb API server.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

    log_level = configure_logging()
    logger.info(
        "Starting codex-lb host=%s port=%s ssl=%s log_level=%s access_log=%s",
        args.host,
        args.port,
        bool(args.ssl_certfile and args.ssl_keyfile),
        log_level,
        False,
    )
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
        access_log=False,
        log_config=build_log_config(log_level),
    )


if __name__ == "__main__":
    main()
