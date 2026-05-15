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
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
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

_POSTGRES_CONTAINER_QUERY_SCRIPT = r"""
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row


def sync_url(database_url):
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    return database_url


def normalize_value(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def normalize_row(row):
    return {key: normalize_value(value) for key, value in dict(row).items()}


payload = json.load(sys.stdin)
database_url = os.environ.get("CODEX_LB_DATABASE_URL")
if not database_url:
    raise RuntimeError("CODEX_LB_DATABASE_URL is not set in container")

output = {}
with psycopg.connect(sync_url(database_url), row_factory=dict_row, connect_timeout=5) as conn:
    with conn.cursor() as cursor:
        for query in payload["queries"]:
            cursor.execute(query["sql"], query.get("params", []))
            output[query["name"]] = [normalize_row(row) for row in cursor.fetchall()]

print(json.dumps(output, separators=(",", ":")))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a small codex-lb live health and hiccup snapshot.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--container-db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--database-url", default=None, help="Optional CODEX_LB_DATABASE_URL to query directly.")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--health-samples", type=int, default=5)
    args = parser.parse_args()

    snapshot: dict[str, Any] = {
        "health": _health(args.base_url, samples=max(args.health_samples, 1)),
        "request_logs": _request_logs(
            args.db_path,
            args.container,
            args.container_db_path,
            args.minutes,
            database_url=args.database_url,
        ),
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
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    if db_path is not None:
        return _request_logs_from_sqlite(db_path, minutes, source={"backend": "sqlite", "path": str(db_path)})

    if database_url is not None:
        return _request_logs_from_database_url(database_url, minutes)

    if shutil.which("docker") is None:
        return {"error": "docker unavailable and neither --db-path nor --database-url was provided"}

    container_database_url = _container_database_url(container)
    backend = _database_backend(container_database_url)
    if backend == "postgresql":
        return _request_logs_from_container_postgres(container, minutes)
    if container_database_url is not None and backend is None:
        return {
            "error": "unsupported container CODEX_LB_DATABASE_URL driver: "
            f"{_database_driver(container_database_url) or 'unknown'}"
        }

    sqlite_container_path = _sqlite_path_from_database_url(container_database_url) or container_db_path
    temp_dir = tempfile.TemporaryDirectory()
    local_db = Path(temp_dir.name) / "store.db"
    copy_error = _copy_sqlite_snapshot(container, sqlite_container_path, local_db)
    if copy_error is not None:
        temp_dir.cleanup()
        return {"error": copy_error}
    try:
        return _request_logs_from_sqlite(
            local_db,
            minutes,
            source={"backend": "sqlite", "container": container, "path": sqlite_container_path},
        )
    finally:
        temp_dir.cleanup()


def _request_logs_from_database_url(database_url: str, minutes: int) -> dict[str, Any]:
    backend = _database_backend(database_url)
    if backend == "postgresql":
        return _request_logs_from_postgres_url(database_url, minutes)
    if backend == "sqlite":
        sqlite_path = _sqlite_path_from_database_url(database_url)
        if sqlite_path is None:
            return {"error": "sqlite database URL does not include a path"}
        return _request_logs_from_sqlite(
            Path(sqlite_path),
            minutes,
            source={"backend": "sqlite", "path": sqlite_path},
        )
    return {"error": f"unsupported database URL driver: {_database_driver(database_url) or 'unknown'}"}


def _request_logs_from_sqlite(db_path: Path, minutes: int, *, source: dict[str, Any]) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        def execute(sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
            return [_normalize_row(dict(row)) for row in conn.execute(sql, params).fetchall()]

        columns = _sqlite_request_log_columns(conn)
        rows = _fetch_request_log_rows(execute, "sqlite", minutes, columns=columns)
    return _build_request_logs_snapshot(rows, minutes, source=source)


def _request_logs_from_postgres_url(database_url: str, minutes: int) -> dict[str, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        return {"error": f"psycopg unavailable for PostgreSQL request-log snapshot: {exc}"}

    try:
        with psycopg.connect(
            _postgres_sync_url(database_url),
            row_factory=cast(Any, dict_row),
            connect_timeout=5,
        ) as conn:

            def execute(sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
                with conn.cursor() as cursor:
                    cursor.execute(cast(Any, sql), params)
                    return [_normalize_row(dict(row)) for row in cursor.fetchall()]

            columns = _postgres_request_log_columns(execute)
            rows = _fetch_request_log_rows(execute, "postgresql", minutes, columns=columns)
    except Exception as exc:  # pragma: no cover - live diagnostic path
        return {"error": f"PostgreSQL request-log query failed: {exc}"}
    return _build_request_logs_snapshot(rows, minutes, source={"backend": "postgresql", "source": "database_url"})


def _request_logs_from_container_postgres(container: str, minutes: int) -> dict[str, Any]:
    columns_or_error = _fetch_postgres_rows_in_container(container, _postgres_request_log_column_query())
    if isinstance(columns_or_error, str):
        return {"error": columns_or_error}
    columns = _request_log_columns_from_rows(columns_or_error["request_log_columns"])
    rows_or_error = _fetch_postgres_rows_in_container(
        container,
        _request_log_queries("postgresql", minutes, columns=columns),
    )
    if isinstance(rows_or_error, str):
        return {"error": rows_or_error}
    return _build_request_logs_snapshot(
        rows_or_error,
        minutes,
        source={"backend": "postgresql", "container": container, "source": "container_env"},
    )


RequestLogDialect = Literal["sqlite", "postgresql"]

_POSTGRES_REQUEST_LOG_COLUMNS_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = current_schema() AND table_name = 'request_logs'
ORDER BY ordinal_position
"""


def _sqlite_request_log_columns(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute("PRAGMA table_info(request_logs)").fetchall()
    return frozenset(str(row["name"]) for row in rows)


def _postgres_request_log_column_query() -> dict[str, tuple[str, Sequence[Any]]]:
    return {"request_log_columns": (_POSTGRES_REQUEST_LOG_COLUMNS_SQL, ())}


def _postgres_request_log_columns(
    execute: Callable[[str, Sequence[Any]], list[dict[str, Any]]],
) -> frozenset[str]:
    return _request_log_columns_from_rows(execute(_POSTGRES_REQUEST_LOG_COLUMNS_SQL, ()))


def _request_log_columns_from_rows(rows: list[dict[str, Any]]) -> frozenset[str]:
    names: list[str] = []
    for row in rows:
        name = row.get("column_name") or row.get("name")
        if isinstance(name, str):
            names.append(name)
    return frozenset(names)


def _fetch_request_log_rows(
    execute: Callable[[str, Sequence[Any]], list[dict[str, Any]]],
    dialect: RequestLogDialect,
    minutes: int,
    *,
    columns: frozenset[str],
) -> dict[str, list[dict[str, Any]]]:
    return {
        name: execute(sql, params)
        for name, (sql, params) in _request_log_queries(dialect, minutes, columns=columns).items()
    }


def _request_log_queries(
    dialect: RequestLogDialect,
    minutes: int,
    *,
    columns: frozenset[str] | None = None,
) -> dict[str, tuple[str, Sequence[Any]]]:
    if dialect == "sqlite":
        window_expr = "datetime('now', ?)"
        params: Sequence[Any] = (f"-{minutes} minutes",)
    else:
        window_expr = "(timezone('utc', now()) - (%s * interval '1 minute'))"
        params = (minutes,)

    has_cost_usd = columns is None or "cost_usd" in columns
    has_request_id = columns is None or "request_id" in columns
    if has_cost_usd and dialect == "postgresql":
        runtime_cost_usd_expr = "round(sum(coalesce(cost_usd, 0.0))::numeric, 6) AS cost_usd"
    elif has_cost_usd:
        runtime_cost_usd_expr = "round(sum(coalesce(cost_usd, 0.0)), 6) AS cost_usd"
    else:
        runtime_cost_usd_expr = "0.0 AS cost_usd"
    request_cost_usd_expr = "cost_usd" if has_cost_usd else "NULL AS cost_usd"
    response_id_expr = "request_id AS response_id" if has_request_id else "NULL AS response_id"

    return {
        "status_rows": (
            f"""
            SELECT status, coalesce(error_code, '') AS error_code, count(*) AS count
            FROM request_logs
            WHERE requested_at >= {window_expr}
            GROUP BY status, coalesce(error_code, '')
            ORDER BY status, coalesce(error_code, '')
            """,
            params,
        ),
        "transport_rows": (
            f"""
            SELECT coalesce(transport, '') AS transport, status, count(*) AS count
            FROM request_logs
            WHERE requested_at >= {window_expr}
            GROUP BY coalesce(transport, ''), status
            ORDER BY coalesce(transport, ''), status
            """,
            params,
        ),
        "service_tier_rows": (
            f"""
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
            WHERE requested_at >= {window_expr}
            GROUP BY 1, 2, 3, 4, 5
            ORDER BY count DESC, avg_latency_ms DESC
            LIMIT 20
            """,
            params,
        ),
        "tier_mismatch_rows": (
            f"""
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
            WHERE requested_at >= {window_expr}
                AND coalesce(requested_service_tier, '') != coalesce(actual_service_tier, '')
            GROUP BY 1, 2, 3, 4
            ORDER BY count DESC, avg_latency_ms DESC
            LIMIT 20
            """,
            params,
        ),
        "latency_rows": (
            f"""
            SELECT status, latency_ms, latency_first_token_ms
            FROM request_logs
            WHERE requested_at >= {window_expr} AND latency_ms IS NOT NULL
            """,
            params,
        ),
        "recent_requests": (
            f"""
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
            WHERE requested_at >= {window_expr}
            ORDER BY requested_at DESC, id DESC
            LIMIT 10
            """,
            params,
        ),
        "slowest_rows": (
            f"""
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
            WHERE requested_at >= {window_expr} AND latency_ms IS NOT NULL
            ORDER BY latency_ms DESC
            LIMIT 10
            """,
            params,
        ),
        "output_bucket_rows": (
            f"""
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
            WHERE requested_at >= {window_expr}
            GROUP BY 1
            ORDER BY avg_latency_ms DESC
            """,
            params,
        ),
        "input_bucket_rows": (
            f"""
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
            WHERE requested_at >= {window_expr}
            GROUP BY 1
            ORDER BY avg_latency_ms DESC
            """,
            params,
        ),
        "runtime_correlation_rows": (
            f"""
            SELECT
                model,
                coalesce(transport, '') AS transport,
                coalesce(reasoning_effort, '') AS reasoning_effort,
                coalesce(requested_service_tier, '') AS requested_service_tier,
                coalesce(actual_service_tier, '') AS actual_service_tier,
                status,
                coalesce(error_code, '') AS error_code,
                CASE
                    WHEN input_tokens IS NULL THEN 'unknown'
                    WHEN input_tokens < 10000 THEN '<10k'
                    WHEN input_tokens < 30000 THEN '10-30k'
                    WHEN input_tokens < 70000 THEN '30-70k'
                    WHEN input_tokens < 150000 THEN '70-150k'
                    ELSE '150k+'
                END AS input_token_bucket,
                CASE
                    WHEN output_tokens IS NULL THEN 'unknown'
                    WHEN output_tokens < 500 THEN '<500'
                    WHEN output_tokens < 1500 THEN '500-1500'
                    WHEN output_tokens < 3000 THEN '1500-3000'
                    WHEN output_tokens < 6000 THEN '3000-6000'
                    ELSE '6000+'
                END AS output_token_bucket,
                count(*) AS count,
                round(avg(latency_ms), 1) AS avg_latency_ms,
                min(latency_ms) AS min_latency_ms,
                max(latency_ms) AS max_latency_ms,
                round(avg(latency_first_token_ms), 1) AS avg_latency_first_token_ms,
                round(avg(input_tokens), 1) AS avg_input_tokens,
                round(avg(output_tokens), 1) AS avg_output_tokens,
                {runtime_cost_usd_expr}
            FROM request_logs
            WHERE requested_at >= {window_expr}
            GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
            ORDER BY count DESC, avg_latency_ms DESC
            LIMIT 25
            """,
            params,
        ),
        "correlation_request_rows": (
            f"""
            SELECT
                requested_at,
                {response_id_expr},
                status,
                model,
                transport,
                service_tier,
                requested_service_tier,
                actual_service_tier,
                reasoning_effort,
                input_tokens,
                output_tokens,
                cached_input_tokens,
                reasoning_tokens,
                {request_cost_usd_expr},
                latency_ms,
                latency_first_token_ms,
                error_code
            FROM request_logs
            WHERE requested_at >= {window_expr}
            ORDER BY requested_at DESC, id DESC
            LIMIT 25
            """,
            params,
        ),
        "recent_errors": (
            f"""
            SELECT requested_at, status, error_code, substr(coalesce(error_message, ''), 1, 160) AS message
            FROM request_logs
            WHERE requested_at >= {window_expr} AND status != 'success'
            ORDER BY requested_at DESC, id DESC
            LIMIT 10
            """,
            params,
        ),
    }


def _build_request_logs_snapshot(
    rows: dict[str, list[dict[str, Any]]],
    minutes: int,
    *,
    source: dict[str, Any],
) -> dict[str, Any]:
    status_rows = rows["status_rows"]
    transport_rows = rows["transport_rows"]
    service_tier_rows = rows["service_tier_rows"]
    tier_mismatch_rows = rows["tier_mismatch_rows"]
    latency_rows = rows["latency_rows"]
    recent_requests = rows["recent_requests"]
    slowest_rows = rows["slowest_rows"]
    output_bucket_rows = rows["output_bucket_rows"]
    input_bucket_rows = rows["input_bucket_rows"]
    runtime_correlation_rows = rows["runtime_correlation_rows"]
    correlation_request_rows = rows["correlation_request_rows"]
    recent_errors = rows["recent_errors"]
    total = sum(row["count"] for row in status_rows)
    successes = sum(row["count"] for row in status_rows if row["status"] == "success")
    return {
        "source": source,
        "window_minutes": minutes,
        "total": total,
        "success_rate": None if total == 0 else round(successes / total, 4),
        "status_counts": status_rows,
        "transport_counts": transport_rows,
        "service_tier_counts": service_tier_rows,
        "tier_mismatches": {
            "count": sum(row["count"] for row in tier_mismatch_rows),
            "groups": tier_mismatch_rows,
        },
        "latency_ms": _latency_summary(row["latency_ms"] for row in latency_rows),
        "success_latency_ms": _latency_summary(row["latency_ms"] for row in latency_rows if row["status"] == "success"),
        "latency_first_token_ms": _latency_summary(row["latency_first_token_ms"] for row in latency_rows),
        "slowest_requests": slowest_rows,
        "output_token_buckets": output_bucket_rows,
        "input_token_buckets": input_bucket_rows,
        "runtime_correlation": {
            "response_id_column": "request_id",
            "groups": runtime_correlation_rows,
            "recent_requests": correlation_request_rows,
        },
        "recent_requests": recent_requests,
        "recent_errors": recent_errors,
    }


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


def _container_database_url(container: str) -> str | None:
    result = subprocess.run(
        ["docker", "inspect", container, "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "CODEX_LB_DATABASE_URL":
            return value
    return None


def _database_driver(database_url: str | None) -> str | None:
    if not database_url:
        return None
    return urlsplit(database_url).scheme or None


def _database_backend(database_url: str | None) -> Literal["sqlite", "postgresql"] | None:
    driver = _database_driver(database_url)
    if driver in {"postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg"}:
        return "postgresql"
    if driver in {"sqlite", "sqlite+aiosqlite"}:
        return "sqlite"
    return None


def _sqlite_path_from_database_url(database_url: str | None) -> str | None:
    if _database_backend(database_url) != "sqlite":
        return None
    path = urlsplit(database_url or "").path
    return path or None


def _postgres_sync_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url.split("://", 1)[1]
    return database_url


def _fetch_postgres_rows_in_container(
    container: str,
    queries: dict[str, tuple[str, Sequence[Any]]],
) -> dict[str, list[dict[str, Any]]] | str:
    payload = {
        "queries": [{"name": name, "sql": sql, "params": list(params)} for name, (sql, params) in queries.items()]
    }
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "python", "-c", _POSTGRES_CONTAINER_QUERY_SCRIPT],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "PostgreSQL request-log query timed out inside container"

    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip() or "container PostgreSQL query failed"
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return f"container PostgreSQL query returned invalid JSON: {exc}"
    if not isinstance(rows, dict):
        return "container PostgreSQL query returned an unexpected payload"
    return rows


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _normalize_value(value) for key, value in row.items()}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


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
