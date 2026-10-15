"""Explicitly invoked advisory migration measurements on an empty disposable PostgreSQL database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEMANTICS = (
    "Advisory subprocess wall time includes CLI startup, inspection and locks. Each revision runs in a separate "
    "CLI invocation and transaction scope; earlier steps remain committed on failure. Alembic autocommit blocks "
    "still apply. This is not the timing or atomicity of one production upgrade."
)


class BenchmarkFailure(Exception):
    """A diagnostic containing only benchmark-owned text."""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url-env", required=True, help="Name of the environment variable containing the disposable PostgreSQL URL."
    )
    parser.add_argument(
        "--disposable",
        required=True,
        action="store_true",
        help="Acknowledge ownership of an empty disposable database.",
    )
    parser.add_argument("--base", required=True, help="Exact Alembic revision ID to seed.")
    parser.add_argument("--target", required=True, help="Exact Alembic revision ID to measure through.")
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--accounts", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory for report.json and report.md.")
    parser.add_argument(
        "--runner-note", default="unspecified", help="Database host/container resource limits and other runner context."
    )
    args = parser.parse_args()
    args.db_url = os.environ.get(args.db_url_env)
    if not args.db_url:
        parser.error("selected URL environment variable must be nonempty")
    if args.rows < 0 or args.accounts < 1 or args.seed < 0 or max(args.rows, args.accounts, args.seed) > 2**31 - 1:
        parser.error("rows and seed must be nonnegative and accounts positive, all within signed 32-bit range")
    return args


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def _source() -> dict[str, Any]:
    untracked = hashlib.sha256()
    for name in _git("ls-files", "--others", "--exclude-standard").splitlines():
        path = ROOT / name
        if path.is_file():
            untracked.update(name.encode() + b"\0" + path.read_bytes())
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "diff_sha256": hashlib.sha256(_git("diff", "HEAD", "--binary").encode()).hexdigest(),
        "untracked_sha256": untracked.hexdigest(),
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    }


def _save(output: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Advisory migration measurements",
        "",
        f"Status: {report['status']}",
        "",
        SEMANTICS,
        "",
        "| Revision | Seconds | Exit |",
        "| --- | ---: | ---: |",
    ]
    for step in report["steps"]:
        lines.append(f"| {step['revision']} | {step['elapsed_seconds']:.6f} | {step['returncode']} |")
    lines.extend(
        ["", "## Provenance and result", "", "```json", json.dumps(report, indent=2, sort_keys=True), "```", ""]
    )
    for name, content in (
        ("report.json", json.dumps(report, indent=2, sort_keys=True) + "\n"),
        ("report.md", "\n".join(lines)),
    ):
        temporary = output / (name + ".tmp")
        temporary.write_text(content)
        temporary.replace(output / name)


def _cli(*arguments: str) -> dict[str, Any]:
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate", *arguments], cwd=ROOT, capture_output=True, text=True
    )
    # Raw exceptions can contain credentials or SQL parameters. Only retain exit state.
    return {"returncode": result.returncode, "elapsed_seconds": time.perf_counter() - start}


def _run(args: argparse.Namespace, report: dict[str, Any]) -> None:
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.db.migration_url import to_sync_database_url
    from scripts.migration_benchmark_fixture import seed_fixture

    url = make_url(args.db_url)
    if url.drivername != "postgresql+asyncpg" or not url.database or url.query:
        raise BenchmarkFailure("use postgresql+asyncpg with an explicit database and no URL query options")
    script = ScriptDirectory(str(ROOT / "app/db/alembic"))
    known = {revision.revision for revision in script.walk_revisions()}
    if args.base not in known or args.target not in known:
        raise BenchmarkFailure("base and target must be exact known Alembic revision IDs")
    ancestors = {revision.revision for revision in script.iterate_revisions(args.target, "base", implicit_base=True)}
    if args.base not in ancestors:
        raise BenchmarkFailure("base must be an ancestor of target")
    revisions = list(reversed(list(script.iterate_revisions(args.target, args.base, implicit_base=True))))
    report["planned_revisions"] = [revision.revision for revision in revisions]
    engine = create_engine(to_sync_database_url(args.db_url))

    def stamps() -> list[str]:
        with engine.connect() as connection:
            if not connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar_one():
                return []
            return list(
                connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars()
            )

    try:
        report["stage"] = "empty_database_check"
        with engine.connect() as connection:
            occupied = connection.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
                    "AND c.relkind IN ('r','p','v','m','S','f')"
                )
            ).scalar_one()
            if occupied:
                raise BenchmarkFailure("target must be empty; benchmark never resets existing data")
            if connection.execute(text("SELECT current_schema()")).scalar_one() != "public":
                raise BenchmarkFailure("target must use the public schema")
            report["database"] = {
                "host": url.host,
                "port": url.port,
                "name": url.database,
                "version": connection.execute(text("SELECT version()")).scalar_one(),
                "settings": dict(
                    connection.execute(
                        text(
                            "SELECT name, setting FROM pg_settings WHERE name IN "
                            "('shared_buffers','work_mem','maintenance_work_mem','max_connections','max_parallel_workers')"
                        )
                    )
                    .tuples()
                    .all()
                ),
            }
        report["stage"] = "base_upgrade"
        _save(args.output, report)
        report["base_upgrade"] = _cli("upgrade", args.base)
        if report["base_upgrade"]["returncode"]:
            raise BenchmarkFailure("base upgrade failed")
        report["stage"] = "fixture"
        _save(args.output, report)
        report["fixture"] = seed_fixture(engine, rows=args.rows, accounts=args.accounts, seed=args.seed)
        _save(args.output, report)
        report["stage"] = "upgrade"
        for revision in revisions:
            report["attempting_revision"] = revision.revision
            _save(args.output, report)
            step = {"revision": revision.revision, **_cli("upgrade", revision.revision)}
            report["steps"].append(step)
            step["resulting_revisions"] = stamps()
            report["resulting_revisions"] = step["resulting_revisions"]
            _save(args.output, report)
            if step["returncode"]:
                raise BenchmarkFailure("revision upgrade failed")
        report["resulting_revisions"] = stamps()
        if report["resulting_revisions"] != [args.target]:
            raise BenchmarkFailure("resulting revision does not match target")
        report["stage"] = "check"
        _save(args.output, report)
        report["check"] = {**_cli("check"), "target_is_head": args.target in script.get_heads()}
        if report["check"]["target_is_head"] and report["check"]["returncode"]:
            raise BenchmarkFailure("public migration check failed at head")
        report["noop"] = not revisions
        report["status"] = "completed"
    finally:
        try:
            report["resulting_revisions"] = stamps()
        finally:
            engine.dispose()


def main() -> None:
    args = _arguments()
    # Pin both routes before importing any application code or launching the CLI.
    os.environ["CODEX_LB_DATABASE_URL"] = args.db_url
    os.environ["CODEX_LB_TEST_DATABASE_URL"] = args.db_url
    source = _source()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "format_version": 1,
        "status": "incomplete",
        "stage": "validation",
        "steps": [],
        "base": args.base,
        "target": args.target,
        "semantics": SEMANTICS,
        "started_at": datetime.now(UTC).isoformat(),
        "runner": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "note": args.runner_note,
        },
        "fixture_parameters": {"rows": args.rows, "accounts": args.accounts, "seed": args.seed},
        "source": source,
    }
    _save(args.output, report)
    try:
        _run(args, report)
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, BenchmarkFailure):
            report["error"] = str(exc)
    report["finished_at"] = datetime.now(UTC).isoformat()
    _save(args.output, report)
    print(f"{report['status']}: {args.output / 'report.json'}")
    raise SystemExit(0 if report["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
