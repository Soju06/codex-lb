from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import AuditLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


async def _wait_for_settings_changed_audit_log(*, attempts: int = 20) -> AuditLog:
    for _ in range(attempts):
        async with SessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == "settings_changed").order_by(AuditLog.id.desc())
            )
            row = result.scalars().first()
            if row is not None:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError("audit log not written for action=settings_changed")


def _default_put_body() -> dict[str, Any]:
    return {
        "stickyThreadsEnabled": True,
        "preferEarlierResetAccounts": True,
        "weeklyPaceWorkingDays": "0,1,2,3,4,5,6",
    }


@pytest.mark.parametrize(
    ("payload_key", "new_value", "audit_field_name"),
    [
        ("stickyThreadsEnabled", False, "sticky_threads_enabled"),
        ("upstreamStreamTransport", "websocket", "upstream_stream_transport"),
        ("preferEarlierResetAccounts", False, "prefer_earlier_reset_accounts"),
        ("routingStrategy", "round_robin", "routing_strategy"),
        (
            "openaiCacheAffinityMaxAgeSeconds",
            180,
            "openai_cache_affinity_max_age_seconds",
        ),
        ("dashboardSessionTtlSeconds", 31536000, "dashboard_session_ttl_seconds"),
        (
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds",
            1800,
            "http_responses_session_bridge_prompt_cache_idle_ttl_seconds",
        ),
        (
            "httpResponsesSessionBridgeGatewaySafeMode",
            True,
            "http_responses_session_bridge_gateway_safe_mode",
        ),
        (
            "stickyReallocationBudgetThresholdPct",
            90.0,
            "sticky_reallocation_budget_threshold_pct",
        ),
        ("importWithoutOverwrite", False, "import_without_overwrite"),
        ("apiKeyAuthEnabled", True, "api_key_auth_enabled"),
        ("weeklyPaceWorkingDays", "0,1,2,3,4", "weekly_pace_working_days"),
    ],
)
@pytest.mark.asyncio
async def test_settings_audit_records_single_changed_field(
    async_client,
    payload_key: str,
    new_value: Any,
    audit_field_name: str,
) -> None:
    body = _default_put_body()
    body[payload_key] = new_value

    response = await async_client.put("/api/settings", json=body)
    assert response.status_code == 200

    audit_log = await _wait_for_settings_changed_audit_log()
    assert audit_log.details is not None, "settings_changed audit row missing details payload"
    details = json.loads(audit_log.details)
    assert audit_field_name in details["changed_fields"], (
        f"settings audit changed_fields missing {audit_field_name!r}; got {details['changed_fields']!r}"
    )


@pytest.mark.asyncio
async def test_settings_audit_changed_fields_excludes_unchanged(async_client) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": True,
        },
    )
    assert response.status_code == 200

    audit_log = await _wait_for_settings_changed_audit_log()
    assert audit_log.details is not None, "settings_changed audit row missing details payload"
    details = json.loads(audit_log.details)
    changed = details["changed_fields"]
    assert changed == ["sticky_threads_enabled"], (
        f"expected only sticky_threads_enabled to be reported; got {changed!r}"
    )


@pytest.mark.asyncio
async def test_settings_audit_changed_fields_empty_on_noop_put(async_client) -> None:
    response = await async_client.put("/api/settings", json=_default_put_body())
    assert response.status_code == 200

    audit_log = await _wait_for_settings_changed_audit_log()
    assert audit_log.details is not None, "settings_changed audit row missing details payload"
    details = json.loads(audit_log.details)
    assert details["changed_fields"] == [], f"no-op PUT should produce an empty changed_fields list; got {details!r}"


@pytest.mark.asyncio
async def test_settings_audit_changed_fields_multi_update(async_client) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "httpResponsesSessionBridgePromptCacheIdleTtlSeconds": 1800,
            "stickyReallocationBudgetThresholdPct": 90.0,
        },
    )
    assert response.status_code == 200

    audit_log = await _wait_for_settings_changed_audit_log()
    assert audit_log.details is not None, "settings_changed audit row missing details payload"
    details = json.loads(audit_log.details)
    changed = set(details["changed_fields"])
    assert changed == {
        "sticky_threads_enabled",
        "prefer_earlier_reset_accounts",
        "http_responses_session_bridge_prompt_cache_idle_ttl_seconds",
        "sticky_reallocation_budget_threshold_pct",
        "sticky_reallocation_primary_budget_threshold_pct",
    }, f"unexpected changed_fields set: {changed!r}"
