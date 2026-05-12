from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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
                reasoning_tokens INTEGER
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
                reasoning_tokens
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
                0
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
                reasoning_tokens
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
