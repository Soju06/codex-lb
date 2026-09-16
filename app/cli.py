from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

_PROCESS_STARTED_NS = time.perf_counter_ns()

if TYPE_CHECKING:
    from app.codex_sessions_retag import RetagResult
    from app.core.runtime_logging import LogConfig


class _CliHelpFormatter(argparse.HelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=36, width=120)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the agent-lb API server.",
        formatter_class=_CliHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    codex_sessions = subparsers.add_parser(
        "codex-sessions",
        help="Manage local Codex session metadata.",
        formatter_class=_CliHelpFormatter,
    )
    codex_sessions_subparsers = codex_sessions.add_subparsers(dest="codex_sessions_command")
    retag = codex_sessions_subparsers.add_parser(
        "retag",
        help="Re-tag Codex threads between the openai and agent-lb model providers.",
        formatter_class=_CliHelpFormatter,
    )
    retag.add_argument(
        "--from", dest="source_provider", metavar="PROVIDER", required=True, help="Provider tag to replace."
    )
    retag.add_argument("--to", dest="target_provider", metavar="PROVIDER", required=True, help="Provider tag to write.")
    retag.add_argument(
        "--codex-home",
        type=Path,
        metavar="PATH",
        default=None,
        help="Codex data directory. Defaults to CODEX_HOME, /codex-home in Docker, or ~/.codex.",
    )
    retag.add_argument("--dry-run", action="store_true", help="Show what would change without writing files.")
    retag.add_argument(
        "--yes",
        action="store_true",
        help="Confirm that Codex/Codex CLI is closed and allow a non-interactive write.",
    )

    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", default=os.getenv("PORT", "2455"))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))
    parser.add_argument(
        "--timeout-keep-alive",
        default=os.getenv("UVICORN_TIMEOUT_KEEP_ALIVE", "7200"),
        help=(
            "Seconds to keep idle HTTP connections open. Codex CLI reuses local "
            "connections for large compact POSTs; short keepalive windows can leave the "
            "client writing to a stale socket before the request reaches the app."
        ),
    )
    parser.add_argument(
        "--timeout-graceful-shutdown",
        default=os.getenv("UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN", "75"),
        help=(
            "Seconds to let in-flight requests finish after SIGTERM before forcing "
            "connections closed. Restarts that skip a drain sever active streams "
            "mid-response (observed 2026-08-18); keep this below the launchd "
            "ExitTimeOut so launchd's SIGKILL never races the drain."
        ),
    )

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    os.environ.setdefault("AGENT_LB_PROCESS_STARTED_NS", str(_PROCESS_STARTED_NS))
    args = _parse_args(argv)

    if args.command == "codex-sessions":
        if args.codex_sessions_command == "retag":
            _run_codex_sessions_retag(args)
            return
        raise SystemExit("codex-sessions requires a subcommand")

    if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

    port = _parse_server_port(args.port)
    timeout_keep_alive = _parse_server_timeout_keep_alive(args.timeout_keep_alive)
    timeout_graceful_shutdown = _parse_server_timeout_graceful_shutdown(args.timeout_graceful_shutdown)
    os.environ["PORT"] = str(port)

    _configure_logging()
    _load_uvicorn().run(
        "app.main:app",
        host=args.host,
        port=port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
        timeout_keep_alive=timeout_keep_alive,
        # Drain in-flight streams on SIGTERM instead of severing them; a
        # restart without a drain surfaces as "Server error mid-response"
        # in every active client session.
        timeout_graceful_shutdown=timeout_graceful_shutdown,
        # Mirror the upstream websocket leg (proxy_websocket): keep sending
        # transport pings so intermediaries see liveness, but disable the pong
        # deadline. Agent clients block their event loop for well over 20s
        # during long local work; uvicorn's default 20s pong timeout was
        # cutting those sessions mid-turn with 1011 "keepalive ping timeout".
        ws_ping_interval=20.0,
        ws_ping_timeout=None,
        # permessage-deflate runs zlib synchronously on the event loop for
        # every outbound frame; with multi-MB streaming deltas it starves the
        # whole proxy (sampled mid-stall 2026-07-11). Clients are on
        # localhost/tailnet, so the bandwidth saving is worthless here.
        ws_per_message_deflate=False,
        # Logging is configured (and its queue listeners started) before
        # uvicorn runs; None keeps uvicorn from re-applying a config whose
        # listeners nobody would start.
        log_config=None,
    )


def _load_uvicorn():
    import uvicorn

    return uvicorn


def _configure_logging() -> "LogConfig":
    from app.core.runtime_logging import configure_runtime_logging

    return configure_runtime_logging()


def _parse_server_port(raw_port: str) -> int:
    try:
        return int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"--port/PORT must be an integer, got {raw_port!r}.") from exc


def _parse_server_timeout_keep_alive(raw_timeout: str) -> int:
    try:
        return int(raw_timeout)
    except ValueError as exc:
        message = f"--timeout-keep-alive/UVICORN_TIMEOUT_KEEP_ALIVE must be an integer, got {raw_timeout!r}."
        raise SystemExit(message) from exc


def _parse_server_timeout_graceful_shutdown(raw_timeout: str) -> int:
    try:
        return int(raw_timeout)
    except ValueError as exc:
        message = (
            "--timeout-graceful-shutdown/UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN must be an "
            f"integer, got {raw_timeout!r}."
        )
        raise SystemExit(message) from exc


def _run_codex_sessions_retag(args: argparse.Namespace) -> None:
    import sqlite3

    from app.codex_sessions_retag import default_codex_home

    codex_home = args.codex_home or default_codex_home()
    if not args.dry_run:
        _confirm_retag_write(args.yes)

    try:
        result = retag_codex_sessions(
            codex_home=codex_home,
            source_provider=args.source_provider,
            target_provider=args.target_provider,
            dry_run=args.dry_run,
            progress_logger=lambda message: print(message, flush=True),
        )
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "locked" in message.casefold():
            message = (
                f"{message}\n"
                "Close Codex/Codex CLI and retry. The state_*.sqlite database can be locked while Codex is running."
            )
        raise SystemExit(message) from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    except OSError as exc:
        raise SystemExit(f"Unable to read or write Codex session files: {exc}") from exc

    _print_retag_summary(result)


def retag_codex_sessions(**kwargs) -> "RetagResult":
    """Lazy compatibility seam for callers and tests that patch this helper."""
    from app.codex_sessions_retag import retag_codex_sessions as implementation

    return implementation(**kwargs)


def _confirm_retag_write(yes: bool) -> None:
    warning = (
        "This command rewrites Codex session metadata, including state_*.sqlite when present.\n"
        "Close Codex/Codex CLI before continuing to avoid SQLite locks or stale writes."
    )
    print(warning, file=sys.stderr)
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to write without --yes in a non-interactive shell.")
    answer = input("Continue? [y/N] ").strip().casefold()
    if answer not in {"y", "yes"}:
        raise SystemExit("Aborted.")


def _print_retag_summary(result: "RetagResult") -> None:
    action = "Would update" if result.dry_run else "Updated"
    methods = ", ".join(result.methods_used) if result.methods_used else "none"
    print("")
    print("Codex session retag summary")
    print(f"- Codex home: {result.codex_home}")
    print(f"- Methods used: {methods}")
    print(f"- JSONL files scanned: {result.jsonl_files_scanned}")
    print(f"- JSONL files matched: {result.jsonl_files_matched}")
    print(f"- SQLite DBs scanned: {result.sqlite_dbs_scanned}")
    print(f"- SQLite DBs matched: {result.sqlite_dbs_matched}")
    print(f"- {action} JSONL files: {result.jsonl_files_matched if result.dry_run else result.jsonl_files_updated}")
    print(f"- {action} SQLite rows: {result.sqlite_rows_matched if result.dry_run else result.sqlite_rows_updated}")
    if result.backup_path is not None:
        print(f"- Backup: {result.backup_path}")


if __name__ == "__main__":
    main()
