#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:2455"
DEFAULT_CONTAINER = "codex-lb-direct"
DEFAULT_DB_PATH = "/var/lib/codex-lb/store.db"

LOG_PATTERNS = (
    "database is locked",
    "Exception in ASGI application",
    "Unexpected error during model fetch",
    "TimeoutError",
    "stream_incomplete",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a small codex-lb live health and hiccup snapshot.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--container-db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--minutes", type=int, default=15)
    args = parser.parse_args()

    snapshot: dict[str, Any] = {
        "health": _health(args.base_url),
        "request_logs": _request_logs(args.db_path, args.container, args.container_db_path, args.minutes),
        "container": _container_state(args.container),
        "log_patterns": _log_patterns(args.container, args.minutes),
    }
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


def _health(base_url: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for path in ("/health/ready", "/backend-api/codex/health"):
        url = base_url.rstrip("/") + path
        try:
            with urlopen(url, timeout=5) as response:  # nosec B310
                checks[path] = {
                    "status": response.status,
                    "body": response.read(1000).decode("utf-8", "replace"),
                }
        except HTTPError as exc:
            checks[path] = {
                "status": exc.code,
                "body": exc.read(1000).decode("utf-8", "replace"),
            }
        except URLError as exc:
            checks[path] = {"error": str(exc.reason)}
    return checks


def _request_logs(
    db_path: Path | None,
    container: str,
    container_db_path: str,
    minutes: int,
) -> dict[str, Any]:
    local_db = db_path
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if local_db is None:
        if shutil.which("docker") is None:
            return {"error": "docker unavailable and --db-path was not provided"}
        temp_dir = tempfile.TemporaryDirectory()
        local_db = Path(temp_dir.name) / "store.db"
        copy = subprocess.run(
            ["docker", "cp", f"{container}:{container_db_path}", str(local_db)],
            check=False,
            capture_output=True,
            text=True,
        )
        if copy.returncode != 0:
            if temp_dir is not None:
                temp_dir.cleanup()
            return {"error": copy.stderr.strip() or copy.stdout.strip() or "docker cp failed"}

    try:
        with sqlite3.connect(str(local_db)) as conn:
            conn.row_factory = sqlite3.Row
            status_rows = conn.execute(
                """
                SELECT status, coalesce(error_code, '') AS error_code, count(*) AS count
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                GROUP BY status, error_code
                ORDER BY status, error_code
                """,
                (f"-{minutes} minutes",),
            ).fetchall()
            recent_errors = conn.execute(
                """
                SELECT requested_at, status, error_code, substr(coalesce(error_message, ''), 1, 160) AS message
                FROM request_logs
                WHERE requested_at >= datetime('now', ?) AND status != 'success'
                ORDER BY requested_at DESC, id DESC
                LIMIT 10
                """,
                (f"-{minutes} minutes",),
            ).fetchall()
            return {
                "window_minutes": minutes,
                "status_counts": [dict(row) for row in status_rows],
                "recent_errors": [dict(row) for row in recent_errors],
            }
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _container_state(container: str) -> dict[str, Any]:
    if shutil.which("docker") is None:
        return {"error": "docker unavailable"}
    result = subprocess.run(
        [
            "docker",
            "inspect",
            container,
            "--format",
            "status={{.State.Status}} restart_count={{.RestartCount}} oom={{.State.OOMKilled}} "
            "started={{.State.StartedAt}} error={{.State.Error}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or result.stdout.strip()}
    return {"summary": result.stdout.strip()}


def _log_patterns(container: str, minutes: int) -> dict[str, Any]:
    if shutil.which("docker") is None:
        return {"error": "docker unavailable"}
    result = subprocess.run(
        ["docker", "logs", "--since", f"{minutes}m", "--timestamps", container],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or result.stdout.strip()}
    lines = result.stdout.splitlines() + result.stderr.splitlines()
    return {
        "window_minutes": minutes,
        "counts": {pattern: sum(1 for line in lines if pattern in line) for pattern in LOG_PATTERNS},
    }


if __name__ == "__main__":
    raise SystemExit(main())
