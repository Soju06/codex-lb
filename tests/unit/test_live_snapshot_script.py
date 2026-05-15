from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import codex_lb_live_snapshot as snapshot
from scripts.codex_lb_live_snapshot import _latency_summary, _request_logs

pytestmark = pytest.mark.unit


def test_latency_summary_reports_nearest_rank_percentiles() -> None:
    assert _latency_summary([100, 200, 300, 400, 500]) == {
        "count": 5,
        "min": 100,
        "avg": 300.0,
        "p50": 300,
        "p90": 500,
        "p95": 500,
        "max": 500,
    }


def test_postgres_runtime_correlation_cost_query_casts_before_rounding() -> None:
    queries = snapshot._request_log_queries(
        "postgresql",
        5,
        columns=frozenset({"cost_usd", "request_id"}),
    )

    sql, _ = queries["runtime_correlation_rows"]

    assert "round(sum(coalesce(cost_usd, 0.0))::numeric, 6) AS cost_usd" in sql


def test_request_logs_snapshot_includes_latency_transport_and_tiers(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE request_logs (
                id INTEGER PRIMARY KEY,
                requested_at DATETIME NOT NULL,
                status VARCHAR NOT NULL,
                error_code VARCHAR,
                error_message TEXT,
                model VARCHAR NOT NULL,
                transport VARCHAR,
                latency_ms INTEGER,
                latency_first_token_ms INTEGER,
                service_tier VARCHAR,
                requested_service_tier VARCHAR,
                actual_service_tier VARCHAR,
                reasoning_effort VARCHAR,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cached_input_tokens INTEGER,
                reasoning_tokens INTEGER,
                cost_usd REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO request_logs (
                requested_at,
                status,
                error_code,
                error_message,
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
                cost_usd
            )
            VALUES (
                datetime('now', '-5 minutes'),
                'success',
                NULL,
                NULL,
                'gpt-5.5',
                'websocket',
                1200,
                250,
                'default',
                'ultrafast',
                'default',
                'low',
                1000,
                100,
                0,
                0,
                0.01
            )
            """
        )
        conn.execute(
            """
            INSERT INTO request_logs (
                requested_at,
                status,
                error_code,
                error_message,
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
                cost_usd
            )
            VALUES (
                datetime('now', '-4 minutes'),
                'error',
                'upstream_unavailable',
                'Request to upstream timed out',
                'gpt-5.5',
                'websocket',
                3000,
                NULL,
                'ultrafast',
                'ultrafast',
                NULL,
                'low',
                NULL,
                NULL,
                NULL,
                NULL,
                NULL
            )
            """
        )
        conn.commit()

    snapshot = _request_logs(db_path, "unused", "/unused.db", 60)

    assert snapshot["status_counts"] == [
        {"status": "error", "error_code": "upstream_unavailable", "count": 1},
        {"status": "success", "error_code": "", "count": 1},
    ]
    assert snapshot["transport_counts"] == [
        {"transport": "websocket", "status": "error", "count": 1},
        {"transport": "websocket", "status": "success", "count": 1},
    ]
    assert {
        "requested_service_tier": "ultrafast",
        "actual_service_tier": "default",
        "service_tier": "default",
        "status": "success",
        "error_code": "",
        "count": 1,
        "avg_latency_ms": 1200.0,
        "min_latency_ms": 1200,
        "max_latency_ms": 1200,
    } in snapshot["service_tier_counts"]
    assert snapshot["tier_mismatches"]["count"] == 2
    assert snapshot["success_rate"] == 0.5
    assert snapshot["latency_ms"] == {
        "count": 2,
        "min": 1200,
        "avg": 2100.0,
        "p50": 1200,
        "p90": 3000,
        "p95": 3000,
        "max": 3000,
    }
    assert snapshot["latency_first_token_ms"] == {
        "count": 1,
        "min": 250,
        "avg": 250.0,
        "p50": 250,
        "p90": 250,
        "p95": 250,
        "max": 250,
    }
    assert snapshot["slowest_requests"][0]["error_code"] == "upstream_unavailable"
    assert snapshot["recent_requests"][0]["error_code"] == "upstream_unavailable"
    assert snapshot["recent_errors"][0]["message"] == "Request to upstream timed out"
    assert snapshot["runtime_correlation"]["response_id_column"] == "request_id"
    assert snapshot["runtime_correlation"]["groups"][0]["model"] == "gpt-5.5"
    assert "cost_usd" in snapshot["runtime_correlation"]["recent_requests"][0]


def test_request_logs_uses_container_postgres_when_live_env_is_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"source": {"backend": "postgresql", "container": "codex-lb-direct"}}
    calls: list[tuple[str, int]] = []

    monkeypatch.setattr(snapshot.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        snapshot,
        "_container_database_url",
        lambda container: "postgresql+asyncpg://user:password@codex-lb-postgres:5432/codex_lb",
    )

    def fake_postgres_snapshot(container: str, minutes: int) -> dict[str, object]:
        calls.append((container, minutes))
        return expected

    monkeypatch.setattr(snapshot, "_request_logs_from_container_postgres", fake_postgres_snapshot)

    assert _request_logs(None, "codex-lb-direct", "/var/lib/codex-lb/store.db", 15) == expected
    assert calls == [("codex-lb-direct", 15)]


def test_postgres_sync_url_removes_sqlalchemy_async_driver() -> None:
    assert (
        snapshot._postgres_sync_url("postgresql+asyncpg://user:password@db:5432/codex_lb")
        == "postgresql://user:password@db:5432/codex_lb"
    )
