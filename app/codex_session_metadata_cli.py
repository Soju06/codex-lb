"""Consumer-neutral CLI for local session metadata repair."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app.codex_session_metadata import SUPPORTED_PROVIDERS, discover_metadata
from app.codex_session_repair import repair_metadata
from app.codex_sessions_retag import default_codex_home


def add_metadata_commands(subparsers: argparse._SubParsersAction) -> None:
    for name in ("metadata-mismatches", "repair-metadata"):
        command = subparsers.add_parser(name, help="Preview or repair selected session metadata.")
        command.add_argument("--provider", required=True, choices=("openai", "codex-lb"))
        command.add_argument("--session-id", action="append", default=[], required=name == "repair-metadata")
        command.add_argument("--codex-home", type=Path)
        command.add_argument("--json", action="store_true")
        if name == "repair-metadata":
            command.add_argument("--yes", action="store_true", help="Confirm Codex is closed and allow scoped writes.")


def run_metadata_command(args: argparse.Namespace) -> None:
    def progress(phase: str, completed: int) -> None:
        if args.json:
            print(json.dumps({"phase": phase, "completed": completed}), file=sys.stderr, flush=True)
        else:
            print(f"{phase}: {completed}", file=sys.stderr, flush=True)

    home = (args.codex_home or default_codex_home()).expanduser().resolve()
    try:
        if args.codex_sessions_command == "repair-metadata":
            if not args.yes:
                raise ValueError("Close Codex/Codex CLI, then pass --yes to confirm session metadata writes.")
            backup = repair_metadata(home, args.provider, args.session_id, progress)
            result = {
                "provider": args.provider,
                "session_ids": sorted(set(args.session_id)),
                "backup_path": str(backup) if backup else None,
            }
        else:
            plan = discover_metadata(home, progress)
            if args.session_id:
                plan = plan.select(args.session_id)
            mismatches = [
                {"session_id": session_id, "providers": sorted(providers, key=lambda value: value or "")}
                for session_id, providers in sorted(plan.providers().items())
                if len(providers) > 1
            ]
            result = {
                "provider": args.provider,
                "mismatches": mismatches,
                "unsupported_sessions": sorted(
                    session_id for session_id, providers in plan.providers().items() if providers - SUPPORTED_PROVIDERS
                ),
            }
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=None if args.json else 2))
