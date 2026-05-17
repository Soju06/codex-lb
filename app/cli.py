from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from app.codex_sessions_retag import RetagResult, default_codex_home, retag_codex_sessions

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

    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "2455")))
    parser.add_argument("--ssl-certfile", default=os.getenv("SSL_CERTFILE"))
    parser.add_argument("--ssl-keyfile", default=os.getenv("SSL_KEYFILE"))

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.command == "codex-sessions":
        if args.codex_sessions_command == "retag":
            _run_codex_sessions_retag(args)
            return
        raise SystemExit("codex-sessions requires a subcommand")

    if bool(args.ssl_certfile) ^ bool(args.ssl_keyfile):
        raise SystemExit("Both --ssl-certfile and --ssl-keyfile must be provided together.")

    os.environ["PORT"] = str(args.port)

    _load_uvicorn().run(
        "app.main:app",
        host=args.host,
        port=args.port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
        log_config=_build_log_config(),
    )


def _load_uvicorn():
    import uvicorn

    return uvicorn


def _build_log_config() -> "LogConfig":
    from app.core.runtime_logging import build_log_config

    return build_log_config()


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


if __name__ == "__main__":
    main()
