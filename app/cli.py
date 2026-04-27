from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

import uvicorn

from app.cli_runtime import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    ServeOptions,
    default_log_file,
    default_pid_file,
    load_running_metadata,
    shutdown_background_server,
    start_background_server,
)
from app.core.runtime_logging import build_log_config


def _add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the codex-lb-cinamon API server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the API server in the foreground.")
    _add_serve_arguments(serve_parser)

    start_parser = subparsers.add_parser("start", help="Start the API server in the background.")
    _add_serve_arguments(start_parser)
    start_parser.add_argument("--pid-file", type=Path, default=default_pid_file())
    start_parser.add_argument("--log-file", type=Path, default=default_log_file())
    start_parser.add_argument("--startup-timeout", type=float, default=DEFAULT_STARTUP_TIMEOUT_SECONDS)

    status_parser = subparsers.add_parser("status", help="Show background server status.")
    status_parser.add_argument("--pid-file", type=Path, default=default_pid_file())

    shutdown_parser = subparsers.add_parser("shutdown", help="Stop the tracked background server.")
    shutdown_parser.add_argument("--pid-file", type=Path, default=default_pid_file())
    shutdown_parser.add_argument("--timeout", type=float, default=DEFAULT_SHUTDOWN_TIMEOUT_SECONDS)

    return parser


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if raw_args and raw_args[0] in {"-h", "--help"}:
        return _build_parser().parse_args(raw_args)
    if not raw_args or raw_args[0].startswith("-"):
        raw_args = ["serve", *raw_args]
    return _build_parser().parse_args(raw_args)


def _serve_options_from_args(args: argparse.Namespace) -> ServeOptions:
    return ServeOptions(
        host=args.host,
        port=args.port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
    )


def _validate_ssl_flags(options: ServeOptions) -> None:
    if bool(options.ssl_certfile) ^ bool(options.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")


def _run_foreground(options: ServeOptions) -> None:
    _validate_ssl_flags(options)
    os.environ["PORT"] = str(options.port)
    uvicorn.run(
        "app.main:app",
        host=options.host,
        port=options.port,
        ssl_certfile=options.ssl_certfile,
        ssl_keyfile=options.ssl_keyfile,
        log_config=build_log_config(),
    )


def _run_background_start(args: argparse.Namespace) -> None:
    options = _serve_options_from_args(args)
    _validate_ssl_flags(options)
    try:
        metadata = start_background_server(
            options,
            pid_file=args.pid_file,
            log_file=args.log_file,
            startup_timeout_seconds=args.startup_timeout,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Started codex-lb-cinamon in background (pid {metadata.pid}, {metadata.host}:{metadata.port})")
    print(f"PID file: {args.pid_file.expanduser()}")
    print(f"Log file: {Path(metadata.log_file).expanduser()}")


def _run_status(args: argparse.Namespace) -> None:
    metadata, stale = load_running_metadata(args.pid_file)
    if metadata is None:
        if stale:
            print(f"No running background server found. Removed stale PID file {args.pid_file.expanduser()}.")
        else:
            print("codex-lb-cinamon background server is not running.")
        raise SystemExit(1)

    print(f"codex-lb-cinamon background server is running (pid {metadata.pid}, {metadata.host}:{metadata.port})")
    print(f"PID file: {args.pid_file.expanduser()}")
    print(f"Log file: {Path(metadata.log_file).expanduser()}")


def _run_shutdown(args: argparse.Namespace) -> None:
    metadata, stale = load_running_metadata(args.pid_file)
    if metadata is None:
        if stale:
            print(f"No running background server found. Removed stale PID file {args.pid_file.expanduser()}.")
        else:
            print("codex-lb-cinamon background server is not running.")
        raise SystemExit(1)

    try:
        stopped = shutdown_background_server(args.pid_file, timeout_seconds=args.timeout)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if stopped is None:
        print("codex-lb-cinamon background server is not running.")
        raise SystemExit(1)

    print(f"Stopped codex-lb-cinamon background server (pid {stopped.pid}).")


def main() -> None:
    args = _parse_args()
    command = args.command

    if command == "serve":
        _run_foreground(_serve_options_from_args(args))
        return
    if command == "start":
        _run_background_start(args)
        return
    if command == "status":
        _run_status(args)
        return
    if command == "shutdown":
        _run_shutdown(args)
        return

    raise SystemExit(f"Unsupported command: {command}")


if __name__ == "__main__":
    main()
