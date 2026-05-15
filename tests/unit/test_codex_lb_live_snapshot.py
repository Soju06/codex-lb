from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _load_snapshot_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "codex_lb_live_snapshot.py"
    spec = importlib.util.spec_from_file_location("codex_lb_live_snapshot", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_request_logs_snapshot_reports_tiers_latency_and_slowest_rows(tmp_path: Path) -> None:
    module = _load_snapshot_module()
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
        conn.executemany(
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
                datetime('now', ?),
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    "-1 minute",
                    "success",
                    None,
                    None,
                    "gpt-5.5",
                    "websocket",
                    1000,
                    200,
                    "default",
                    "ultrafast",
                    "default",
                    "low",
                    1000,
                    100,
                    0,
                    0,
                    0.01,
                ),
                (
                    "-2 minutes",
                    "success",
                    None,
                    None,
                    "gpt-5.5",
                    "websocket",
                    90000,
                    500,
                    "default",
                    "ultrafast",
                    "default",
                    "high",
                    60000,
                    3200,
                    1000,
                    500,
                    0.32,
                ),
                (
                    "-3 minutes",
                    "error",
                    "stream_incomplete",
                    "Upstream websocket closed before response.completed",
                    "gpt-5.5",
                    "websocket",
                    5000,
                    None,
                    "ultrafast",
                    "ultrafast",
                    None,
                    "low",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            ],
        )

    snapshot = module._request_logs(db_path, "unused", "/unused.db", 10)

    assert snapshot["total"] == 3
    assert snapshot["success_rate"] == 0.6667
    assert snapshot["latency_ms"]["p95"] == 90000
    assert snapshot["success_latency_ms"]["count"] == 2
    assert snapshot["tier_mismatches"]["count"] == 3
    assert snapshot["slowest_requests"][0]["latency_ms"] == 90000
    assert snapshot["slowest_requests"][0]["output_tokens"] == 3200
    assert snapshot["output_token_buckets"][0]["bucket"] == "3000-6000"
    assert snapshot["recent_errors"][0]["error_code"] == "stream_incomplete"
    assert snapshot["runtime_correlation"]["groups"][0]["model"] == "gpt-5.5"
    assert any(
        row["error_code"] == "stream_incomplete" for row in snapshot["runtime_correlation"]["recent_requests"]
    )
