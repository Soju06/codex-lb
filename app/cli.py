from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from app.codex_sessions_retag import RetagResult, default_codex_home, retag_codex_sessions
from app.core.config.settings import get_settings

if TYPE_CHECKING:
    from app.core.runtime_logging import LogConfig


class _CliHelpFormatter(argparse.HelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=36, width=120)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the codex-lb API server.",
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
        help="Re-tag Codex threads between the openai and codex-lb model providers.",
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

    request_logs = subparsers.add_parser(
        "request-logs",
        help="Manage request-log storage.",
        formatter_class=_CliHelpFormatter,
    )
    request_logs_subparsers = request_logs.add_subparsers(dest="request_logs_command")
    prune = request_logs_subparsers.add_parser(
        "prune",
        help="Roll up and prune old raw request logs. Dry-run by default.",
        formatter_class=_CliHelpFormatter,
    )
    prune.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Raw request-log retention window. Defaults to CODEX_LB_REQUEST_LOG_RETENTION_DAYS.",
    )
    prune.add_argument("--apply", action="store_true", help="Write aggregates and delete eligible raw rows.")

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

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.command == "codex-sessions":
        if args.codex_sessions_command == "retag":
            _run_codex_sessions_retag(args)
            return
        raise SystemExit("codex-sessions requires a subcommand")
    if args.command == "request-logs":
        if args.request_logs_command == "prune":
            anyio.run(_run_request_logs_prune, args)
            return
        raise SystemExit("request-logs requires a subcommand")

    if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

    port = _parse_server_port(args.port)
    timeout_keep_alive = _parse_server_timeout_keep_alive(args.timeout_keep_alive)
    os.environ["PORT"] = str(port)

    _load_uvicorn().run(
        "app.main:app",
        host=args.host,
        port=port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
        timeout_keep_alive=timeout_keep_alive,
        log_config=_build_log_config(),
    )


def _load_uvicorn():
    import uvicorn

    return uvicorn


def _build_log_config() -> "LogConfig":
    from app.core.runtime_logging import build_log_config

    return build_log_config()


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


def _run_codex_sessions_retag(args: argparse.Namespace) -> None:
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


def _print_retag_summary(result: RetagResult) -> None:
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


async def _run_request_logs_prune(args: argparse.Namespace) -> None:
    from app.db.session import SessionLocal, close_db, init_db
    from app.modules.request_logs.retention import RequestLogRetentionService

    settings = get_settings()
    retention_days = args.retention_days if args.retention_days is not None else settings.request_log_retention_days
    try:
        await init_db()
        async with SessionLocal() as session:
            result = await RequestLogRetentionService(session).run(
                retention_days=retention_days,
                dry_run=not args.apply,
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        await close_db()

    action = "Would prune" if result.dry_run else "Pruned"
    print("")
    print("Request log retention summary")
    print(f"- Mode: {'dry-run' if result.dry_run else 'apply'}")
    print(f"- Retention days: {result.retention_days}")
    print(f"- Cutoff: {result.cutoff.isoformat()}")
    print(f"- Eligible raw rows: {result.eligible_rows}")
    print(f"- Aggregate groups: {result.aggregate_groups}")
    print(f"- Aggregate rows written: {result.aggregate_rows_written}")
    print(f"- {action} raw rows: {result.raw_rows_deleted if not result.dry_run else result.eligible_rows}")


if __name__ == "__main__":
    main()
