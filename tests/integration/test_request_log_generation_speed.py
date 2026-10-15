from __future__ import annotations

import pytest

from app.db.models import RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_request_api_qualifies_generation_speed_samples(async_client, db_setup):
    cases = [
        ("reasoning_first", 1000, 1000, 200, 800, 3, 200, 40, "success", 800.0, "estimated"),
        ("slow_settlement", 3000, 1000, 200, 800, 3, 200, 40, "success", 800.0, "estimated"),
        ("short_burst", 2005, 2005, 2000, 2000, 2, 120, 110, "success", None, "insufficient_sample"),
        ("single_chunk", 4000, 4000, 3000, 3000, 1, 120, 110, "success", None, "insufficient_sample"),
        ("legacy", 1000, None, 200, None, None, 200, 40, "success", 200.0, "legacy_estimate"),
        ("legacy_short", 2005, None, 2000, None, None, 120, 110, "success", None, "insufficient_sample"),
        ("unknown_reasoning", 1000, 1000, 200, 800, 3, 200, None, "success", None, "missing_usage"),
        ("cancelled", 1000, 1000, 200, 800, 3, 200, 40, "cancelled", None, "incomplete"),
        ("backwards_output", 1000, 1000, 500, 200, 3, 200, 40, "success", None, "invalid_sample"),
        ("late_terminal", 1000, 1500, 200, 800, 3, 200, 40, "success", None, "invalid_sample"),
        ("early_terminal", 1000, 700, 200, 800, 3, 200, 40, "success", None, "invalid_sample"),
        ("missing_terminal", 1000, None, 200, 800, 3, 200, 40, "success", None, "missing_timing"),
        ("missing_first", 1000, 1000, None, None, 0, 200, 40, "success", None, "missing_timing"),
        ("terminal_only_metadata", 1000, 1000, 200, None, None, 200, 40, "success", None, "missing_timing"),
        ("high_qualified", 1000, 1000, 200, 200, 3, 2000, 0, "success", 2500.0, "estimated"),
        ("minimum_window", 1000, 1000, 200, 900, 2, 200, 0, "success", 2000.0, "estimated"),
    ]
    async with SessionLocal() as session:
        for name, total, terminal, ttft, first, count, output, reasoning, status, _, _ in cases:
            session.add(
                RequestLog(
                    request_id=name,
                    model="gpt-5.6-sol",
                    status=status,
                    output_tokens=output,
                    reasoning_tokens=reasoning,
                    latency_ms=total,
                    latency_upstream_terminal_ms=terminal,
                    latency_first_token_ms=ttft,
                    latency_first_output_ms=first,
                    output_delta_count=count,
                )
            )
        await session.commit()
    response = await async_client.get("/api/request-logs?limit=20")
    assert response.status_code == 200
    rows = {row["requestId"]: row for row in response.json()["requests"]}
    for name, total, terminal, _, first, count, _, _, _, speed, quality in cases:
        assert rows[name]["generationTps"] == speed
        assert rows[name]["generationTpsStatus"] == quality
        assert rows[name]["latencyFirstOutputMs"] == first
        assert rows[name]["outputDeltaCount"] == count
        assert rows[name]["latencyMs"] == total
        assert rows[name]["latencyUpstreamTerminalMs"] == terminal
