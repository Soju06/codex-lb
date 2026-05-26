from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import Sequence

import uvicorn

from app.core.runtime_logging import build_log_config


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or initialize the codex-lb API server.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "init"),
        default="serve",
        help="Use `serve` to start the API server or `init` to create a local user config file.",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="When used with `init`, overwrite an existing .env.local file.",
    )
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

    return parser.parse_args(args=argv)


def _init_runtime(*, force: bool = False, base_dir: Path | None = None, home_dir: Path | None = None) -> Path:
    from app.core.config.settings import BASE_DIR, DEFAULT_HOME_DIR

    resolved_base_dir = BASE_DIR if base_dir is None else base_dir
    resolved_home_dir = DEFAULT_HOME_DIR if home_dir is None else home_dir
    resolved_home_dir.mkdir(parents=True, exist_ok=True)

    example_path = resolved_base_dir / ".env.example"
    env_path = resolved_home_dir / ".env.local"
    if force or not env_path.exists():
        if example_path.exists():
            shutil.copyfile(example_path, env_path)
        else:
            env_path.touch()

    print(f"User config path: {env_path}")
    print("Edit the file if needed, then run `codex-lb` to start the service.")
    return env_path


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.command == "init":
        _init_runtime(force=args.force)
        return

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
