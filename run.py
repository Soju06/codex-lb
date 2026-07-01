#!/usr/bin/env python3
"""Entrypoint: load config.toml and serve the middleware with uvicorn."""
from __future__ import annotations

import logging
from pathlib import Path

import uvicorn

from middleware.app import create_app
from middleware.config import load_config

ROOT = Path(__file__).resolve().parent


def main() -> None:
    cfg = load_config(ROOT / "config.toml")
    logging.basicConfig(level=getattr(logging, cfg.log.level.upper(), logging.INFO))
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level=cfg.log.level)


if __name__ == "__main__":
    main()
