from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import AuditLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


async def _wait_for_settings_changed_audit_log(*, after_id: int | None = None, attempts: int = 20) -> AuditLog:
    for _ in range(attempts):
        async with SessionLocal() as session:
            filters = [AuditLog.action == "settings_changed"]
            if after_id is not None:
                filters.append(AuditLog.id > after_id)
            result = await session.execute(select(AuditLog).where(*filters).order_by(AuditLog.id.desc()))
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
        ("prohibitFastMode", True, "prohibit_fast_mode"),
        ("preferEarlierResetAccounts", False, "prefer_earlier_reset_accounts"),
        ("showResetCreditBadges", False, "show_reset_credit_badges"),
        (
            "autoRedeemResetCreditsBeforeExpiry",
            True,
            "auto_redeem_reset_credits_before_expiry",
        ),
        ("showResetCreditExpiryBadge", False, "show_reset_credit_expiry_badge"),
        ("routingStrategy", "round_robin", "routing_strategy"),
        (
            "openaiCacheAffinityMaxAgeSeconds",
            180,
            "openai_cache_affinity_max_age_seconds",
        ),
        ("dashboardSessionTtlSeconds", 604800, "dashboard_session_ttl_seconds"),
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
        (
            "limitWarmupExhaustedThresholdPercent",
            98.5,
            "limit_warmup_exhausted_threshold_percent",
        ),
        (
            "limitWarmupIdleThresholdPercent",
            2.0,
            "limit_warmup_idle_threshold_percent",
        ),
        ("weeklyPaceWorkingDays", "0,1,2,3,4", "weekly_pace_working_days"),
        ("limitWarmupStaggeredIdleEnabled", True, "limit_warmup_staggered_idle_enabled"),
        ("hideUpstreamQuotaFromApiKeys", True, "hide_upstream_quota_from_api_keys"),
        ("requestLogRetentionOverrideDays", 30, "request_log_retention_override_days"),
        ("usageHistoryRetentionOverrideDays", 45, "usage_history_retention_override_days"),
        ("conversationArchiveEnabled", True, "conversation_archive_enabled"),  # M5 conversation archive
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
async def test_settings_audit_records_capacity_override_clear_when_effective_is_unchanged(async_client) -> None:
    pinned = await async_client.put("/api/settings", json={"proxyAccountStreamLimit": 8})
    assert pinned.status_code == 200
    pinned_audit_log = await _wait_for_settings_changed_audit_log()

    cleared = await async_client.put("/api/settings", json={"proxyAccountStreamLimit": None})
    assert cleared.status_code == 200

    audit_log = await _wait_for_settings_changed_audit_log(after_id=pinned_audit_log.id)
    assert audit_log.details is not None, "settings_changed audit row missing details payload"
    details = json.loads(audit_log.details)
    assert "proxy_account_stream_limit" in details["changed_fields"]


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


async def _create_responses_model_source(async_client, name: str) -> str:
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": "http://127.0.0.1:9/v1",
            "supportsChatCompletions": True,
            "supportsResponses": True,
            "models": [{"model": "gpt-5.1", "supportsStreaming": True}],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.asyncio
async def test_settings_audit_records_subscription_overflow_designation_and_drain(async_client) -> None:
    """The overflow designation cannot ride the parametrized table above: its value
    must be a real Responses-capable source id, and clearing it also arms the
    read-only drain deadline, which must be reported as its own changed field."""
    source_id = await _create_responses_model_source(async_client, "overflow-audit")

    designated = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": source_id})
    assert designated.status_code == 200
    designated_log = await _wait_for_settings_changed_audit_log()
    assert designated_log.details is not None
    assert json.loads(designated_log.details)["changed_fields"] == ["subscription_overflow_source_id"]

    cleared = await async_client.put("/api/settings", json={"subscriptionOverflowSourceId": None})
    assert cleared.status_code == 200
    cleared_log = await _wait_for_settings_changed_audit_log(after_id=designated_log.id)
    assert cleared_log.details is not None
    assert json.loads(cleared_log.details)["changed_fields"] == [
        "subscription_overflow_source_id",
        "subscription_overflow_drain_until",
    ]


@pytest.mark.asyncio
async def test_model_context_window_override_writes_are_audited(async_client) -> None:
    """A row that changes what the catalog advertises to every client is audited
    like any other dashboard settings write, on both store and delete."""
    path = "/api/settings/model-context-window-overrides"

    stored = await async_client.put(f"{path}/gpt-5.4", json={"contextWindow": 515_000})
    assert stored.status_code == 200
    store_log = await _wait_for_settings_changed_audit_log()
    assert store_log.details is not None
    store_details = json.loads(store_log.details)
    assert store_details["changed_fields"] == ["model_context_window_overrides"]
    assert store_details["slug"] == "gpt-5.4"

    removed = await async_client.delete(f"{path}/gpt-5.4")
    assert removed.status_code == 200
    delete_log = await _wait_for_settings_changed_audit_log(after_id=store_log.id)
    assert delete_log.details is not None
    assert json.loads(delete_log.details)["slug"] == "gpt-5.4"


# M5 conversation archive
async def _wait_for_audit_log(action: str, *, after_id: int | None = None, attempts: int = 20) -> AuditLog | None:
    for _ in range(attempts):
        async with SessionLocal() as session:
            filters = [AuditLog.action == action]
            if after_id is not None:
                filters.append(AuditLog.id > after_id)
            result = await session.execute(select(AuditLog).where(*filters).order_by(AuditLog.id.desc()))
            row = result.scalars().first()
            if row is not None:
                return row
        await asyncio.sleep(0.05)
    return None


@pytest.mark.asyncio
async def test_conversation_archive_toggle_writes_a_dedicated_audit_event_with_actor(async_client) -> None:
    """Every effective on/off flip of the prompt recorder is its own audit line naming the actor."""
    from app.core.conversation_archive import CONVERSATION_ARCHIVE_TOGGLED_ACTION

    enabled = await async_client.put("/api/settings", json={"conversationArchiveEnabled": True})
    assert enabled.status_code == 200
    on_event = await _wait_for_audit_log(CONVERSATION_ARCHIVE_TOGGLED_ACTION)
    assert on_event is not None, "conversation_archive_toggled audit row not written when enabling"
    assert on_event.details is not None
    on_details = json.loads(on_event.details)
    assert on_details["enabled"] is True
    assert on_details["source"] == "dashboard"
    assert on_details["actor_role"] == "admin"
    assert "actor" in on_details
    assert on_event.actor_ip is not None

    # Storing the same value again is not a flip: no second event.
    same = await async_client.put("/api/settings", json={"conversationArchiveEnabled": True})
    assert same.status_code == 200
    await _wait_for_settings_changed_audit_log(after_id=on_event.id)
    assert await _wait_for_audit_log(CONVERSATION_ARCHIVE_TOGGLED_ACTION, after_id=on_event.id, attempts=3) is None

    # Clearing the dashboard value with the env alias off is an effective flip
    # to off and is audited as such.
    cleared = await async_client.put("/api/settings", json={"conversationArchiveEnabled": None})
    assert cleared.status_code == 200
    off_event = await _wait_for_audit_log(CONVERSATION_ARCHIVE_TOGGLED_ACTION, after_id=on_event.id)
    assert off_event is not None, "conversation_archive_toggled audit row not written when disabling"
    assert off_event.details is not None
    off_details = json.loads(off_event.details)
    assert off_details["enabled"] is False
    assert off_details["source"] == "default"
    assert off_details["actor_role"] == "admin"


# end M5 conversation archive


@pytest.mark.asyncio
async def test_conversation_archive_toggle_audit_names_the_actor_under_trusted_header_auth(
    async_client, monkeypatch
) -> None:
    """When the auth mode carries an identity, the dedicated event records it.

    A proxy-asserted identity is resolved to a dashboard account (provisioned on
    first arrival), so the acting principal the event names is that account: its
    username, which is the subject with ``@`` folded to ``.``. The companion
    ``settings_changed`` row carries the same account in its actor columns.
    """
    from app.core.auth.dashboard_mode import DashboardAuthMode
    from app.core.config.settings import get_settings
    from app.core.conversation_archive import CONVERSATION_ARCHIVE_TOGGLED_ACTION
    from app.modules.dashboard_users.identity_resolver import slugify_subject

    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    get_settings.cache_clear()

    subject = "alice@example.com"
    enabled = await async_client.put(
        "/api/settings",
        json={"conversationArchiveEnabled": True},
        headers={"Remote-User": subject},
    )
    assert enabled.status_code == 200

    event = await _wait_for_audit_log(CONVERSATION_ARCHIVE_TOGGLED_ACTION)
    assert event is not None
    assert event.details is not None
    details = json.loads(event.details)
    assert details["actor"] == slugify_subject(subject) == "alice.example.com"
    assert details["actor_role"] == "admin"
    assert details["enabled"] is True

    settings_changed = await _wait_for_settings_changed_audit_log()
    assert settings_changed.actor_username == details["actor"]
    assert settings_changed.actor_user_id is not None
    assert settings_changed.auth_method == "trusted_header"


@pytest.mark.asyncio
async def test_unrelated_settings_change_writes_no_conversation_archive_event(async_client) -> None:
    """The dedicated event is reserved for effective archive flips."""
    from app.core.conversation_archive import CONVERSATION_ARCHIVE_TOGGLED_ACTION

    response = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
    assert response.status_code == 200
    await _wait_for_settings_changed_audit_log()

    assert await _wait_for_audit_log(CONVERSATION_ARCHIVE_TOGGLED_ACTION, attempts=3) is None
