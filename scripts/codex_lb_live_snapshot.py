#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import subprocess
import tempfile
import time
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
    parser.add_argument("--health-samples", type=int, default=5)
    args = parser.parse_args()

    snapshot: dict[str, Any] = {
        "health": _health(args.base_url, samples=max(args.health_samples, 1)),
        "request_logs": _request_logs(args.db_path, args.container, args.container_db_path, args.minutes),
        "container": _container_state(args.container),
        "log_patterns": _log_patterns(args.container, args.minutes),
    }
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


def _health(base_url: str, *, samples: int) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for path in ("/health/ready", "/backend-api/codex/health"):
        url = base_url.rstrip("/") + path
        statuses: list[int] = []
        bodies: list[str] = []
        errors: list[str] = []
        latencies_ms: list[float] = []
        for _ in range(samples):
            started = time.perf_counter()
            try:
                with urlopen(url, timeout=5) as response:  # nosec B310
                    statuses.append(response.status)
                    bodies.append(response.read(1000).decode("utf-8", "replace"))
            except HTTPError as exc:
                statuses.append(exc.code)
                bodies.append(exc.read(1000).decode("utf-8", "replace"))
            except URLError as exc:
                errors.append(str(exc.reason))
            finally:
                latencies_ms.append((time.perf_counter() - started) * 1000)
        checks[path] = {
            "statuses": statuses,
            "errors": errors,
            "body": bodies[-1] if bodies else None,
            "latency_ms": _latency_summary(latencies_ms),
        }
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
        copy_error = _copy_sqlite_snapshot(container, container_db_path, local_db)
        if copy_error is not None:
            if temp_dir is not None:
                temp_dir.cleanup()
            return {"error": copy_error}

    try:
        with sqlite3.connect(str(local_db)) as conn:
            conn.row_factory = sqlite3.Row
            window = f"-{minutes} minutes"
            status_rows = conn.execute(
                """
                SELECT status, coalesce(error_code, '') AS error_code, count(*) AS count
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                GROUP BY status, error_code
                ORDER BY status, error_code
                """,
                (window,),
            ).fetchall()
            transport_rows = conn.execute(
                """
                SELECT coalesce(transport, '') AS transport, status, count(*) AS count
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                GROUP BY transport, status
                ORDER BY transport, status
                """,
                (window,),
            ).fetchall()
            service_tier_rows = conn.execute(
                """
                SELECT
                    coalesce(requested_service_tier, '') AS requested_service_tier,
                    coalesce(actual_service_tier, '') AS actual_service_tier,
                    coalesce(service_tier, '') AS service_tier,
                    status,
                    coalesce(error_code, '') AS error_code,
                    count(*) AS count,
                    round(avg(latency_ms), 1) AS avg_latency_ms,
                    min(latency_ms) AS min_latency_ms,
                    max(latency_ms) AS max_latency_ms
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                GROUP BY requested_service_tier, actual_service_tier, service_tier, status, error_code
                ORDER BY count DESC, avg_latency_ms DESC
                LIMIT 20
                """,
                (window,),
            ).fetchall()
            tier_mismatch_rows = conn.execute(
                """
                SELECT
                    coalesce(requested_service_tier, '') AS requested_service_tier,
                    coalesce(actual_service_tier, '') AS actual_service_tier,
                    status,
                    coalesce(error_code, '') AS error_code,
                    count(*) AS count,
                    round(avg(latency_ms), 1) AS avg_latency_ms,
                    min(latency_ms) AS min_latency_ms,
                    max(latency_ms) AS max_latency_ms
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                    AND coalesce(requested_service_tier, '') != coalesce(actual_service_tier, '')
                GROUP BY requested_service_tier, actual_service_tier, status, error_code
                ORDER BY count DESC, avg_latency_ms DESC
                LIMIT 20
                """,
                (window,),
            ).fetchall()
            latency_rows = conn.execute(
                """
                SELECT status, latency_ms, latency_first_token_ms
                FROM request_logs
                WHERE requested_at >= datetime('now', ?) AND latency_ms IS NOT NULL
                """,
                (window,),
            ).fetchall()
            recent_requests = conn.execute(
                """
                SELECT
                    requested_at,
                    status,
                    model,
                    transport,
                    latency_ms,
                    latency_first_token_ms,
                    service_tier,
                    requested_service_tier,
                    actual_service_tier,
                    error_code
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                ORDER BY requested_at DESC, id DESC
                LIMIT 10
                """,
                (window,),
            ).fetchall()
            slowest_rows = conn.execute(
                """
                SELECT
                    requested_at,
                    status,
                    model,
                    transport,
                    latency_ms,
                    latency_first_token_ms,
                    service_tier,
                    requested_service_tier,
                    actual_service_tier,
                    reasoning_effort,
                    input_tokens,
                    output_tokens,
                    cached_input_tokens,
                    reasoning_tokens,
                    error_code
                FROM request_logs
                WHERE requested_at >= datetime('now', ?) AND latency_ms IS NOT NULL
                ORDER BY latency_ms DESC
                LIMIT 10
                """,
                (window,),
            ).fetchall()
            output_bucket_rows = conn.execute(
                """
                SELECT
                    CASE
                        WHEN output_tokens IS NULL THEN 'unknown'
                        WHEN output_tokens < 500 THEN '<500'
                        WHEN output_tokens < 1500 THEN '500-1500'
                        WHEN output_tokens < 3000 THEN '1500-3000'
                        WHEN output_tokens < 6000 THEN '3000-6000'
                        ELSE '6000+'
                    END AS bucket,
                    count(*) AS count,
                    round(avg(latency_ms), 1) AS avg_latency_ms,
                    min(latency_ms) AS min_latency_ms,
                    max(latency_ms) AS max_latency_ms,
                    round(avg(input_tokens), 1) AS avg_input_tokens
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                GROUP BY bucket
                ORDER BY avg_latency_ms DESC
                """,
                (window,),
            ).fetchall()
            input_bucket_rows = conn.execute(
                """
                SELECT
                    CASE
                        WHEN input_tokens IS NULL THEN 'unknown'
                        WHEN input_tokens < 10000 THEN '<10k'
                        WHEN input_tokens < 30000 THEN '10-30k'
                        WHEN input_tokens < 70000 THEN '30-70k'
                        WHEN input_tokens < 150000 THEN '70-150k'
                        ELSE '150k+'
                    END AS bucket,
                    count(*) AS count,
                    round(avg(latency_ms), 1) AS avg_latency_ms,
                    min(latency_ms) AS min_latency_ms,
                    max(latency_ms) AS max_latency_ms,
                    round(avg(output_tokens), 1) AS avg_output_tokens
                FROM request_logs
                WHERE requested_at >= datetime('now', ?)
                GROUP BY bucket
                ORDER BY avg_latency_ms DESC
                """,
                (window,),
            ).fetchall()
            recent_errors = conn.execute(
                """
                SELECT requested_at, status, error_code, substr(coalesce(error_message, ''), 1, 160) AS message
                FROM request_logs
                WHERE requested_at >= datetime('now', ?) AND status != 'success'
                ORDER BY requested_at DESC, id DESC
                LIMIT 10
                """,
                (window,),
            ).fetchall()
            total = sum(row["count"] for row in status_rows)
            successes = sum(row["count"] for row in status_rows if row["status"] == "success")
            return {
                "window_minutes": minutes,
                "total": total,
                "success_rate": None if total == 0 else round(successes / total, 4),
                "status_counts": [dict(row) for row in status_rows],
                "transport_counts": [dict(row) for row in transport_rows],
                "service_tier_counts": [dict(row) for row in service_tier_rows],
                "tier_mismatches": {
                    "count": sum(row["count"] for row in tier_mismatch_rows),
                    "groups": [dict(row) for row in tier_mismatch_rows],
                },
                "latency_ms": _latency_summary(row["latency_ms"] for row in latency_rows),
                "success_latency_ms": _latency_summary(
                    row["latency_ms"] for row in latency_rows if row["status"] == "success"
                ),
                "latency_first_token_ms": _latency_summary(row["latency_first_token_ms"] for row in latency_rows),
                "slowest_requests": [dict(row) for row in slowest_rows],
                "output_token_buckets": [dict(row) for row in output_bucket_rows],
                "input_token_buckets": [dict(row) for row in input_bucket_rows],
                "recent_requests": [dict(row) for row in recent_requests],
                "recent_errors": [dict(row) for row in recent_errors],
            }
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _copy_sqlite_snapshot(container: str, container_db_path: str, local_db: Path) -> str | None:
    copy = subprocess.run(
        ["docker", "cp", f"{container}:{container_db_path}", str(local_db)],
        check=False,
        capture_output=True,
        text=True,
    )
    if copy.returncode != 0:
        return copy.stderr.strip() or copy.stdout.strip() or "docker cp failed"
    for suffix in ("-wal", "-shm"):
        subprocess.run(
            ["docker", "cp", f"{container}:{container_db_path}{suffix}", str(local_db) + suffix],
            check=False,
            capture_output=True,
            text=True,
        )
    return None


def _latency_summary(values: Any) -> dict[str, int | float] | None:
    latencies = sorted(int(value) for value in values if isinstance(value, int | float))
    if not latencies:
        return None
    total = sum(latencies)
    return {
        "count": len(latencies),
        "min": latencies[0],
        "avg": round(total / len(latencies), 1),
        "p50": _percentile(latencies, 50),
        "p90": _percentile(latencies, 90),
        "p95": _percentile(latencies, 95),
        "max": latencies[-1],
    }


def _percentile(values: list[int], percentile: int) -> int:
    index = max(0, math.ceil((percentile / 100) * len(values)) - 1)
    return values[index]


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
