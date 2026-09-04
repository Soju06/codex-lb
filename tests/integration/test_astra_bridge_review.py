from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.types import JsonValue
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_astra_inherited_policy import _reasoning_key
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


async def _bridge(async_client, app_instance, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    monkeypatch.setattr(
        proxy_module.get_settings(),
        "http_responses_session_bridge_ambiguous_continuation_recovery_mode",
        "server_indefinite_recovery",
    )
    account_id = await _import_account(async_client, "astra-review", "astra-review@example.com")
    account = await _get_account(account_id)
    upstream = _FakeBridgeUpstreamWebSocket()
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None, error_code=None)),
    )

    async def fresh(self, target, **kwargs):
        return target

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", AsyncMock(return_value=upstream))
    return get_proxy_service_for_app(app_instance), upstream


async def _close_bridge(service):
    async with service._http_bridge_lock:
        sessions = list(service._http_bridge_sessions.values())
    for session in sessions:
        await service._close_http_bridge_session(session)
    assert await service.drain_persistence_tasks(timeout_seconds=1)


@pytest.mark.parametrize("model", ["gpt-6-astra", "GPT-6-ASTRA", " gpt-6-astra "])
async def test_http_bridge_normalizes_astra_ultra_egress(async_client, app_instance, monkeypatch, model):
    service, upstream = await _bridge(async_client, app_instance, monkeypatch)
    try:
        response = await async_client.post(
            "/v1/responses",
            json={
                "model": model,
                "instructions": "",
                "input": [
                    {"role": "user", "content": "Begin"},
                    {"type": "configuration_update", "reasoning": {"effort": "ultra"}},
                    {"role": "user", "content": "Continue"},
                ],
            },
        )
        assert response.status_code == 200, response.text
        assert len(upstream.sent_text) == 1
        wire = json.loads(upstream.sent_text[0])
        assert wire["input"][1]["reasoning"]["effort"] == "max"
    finally:
        await _close_bridge(service)


@pytest.mark.parametrize("anchor_source", ["hard_turn", "latest_completed"])
@pytest.mark.parametrize("validation", ["forbidden_effort", "incompatible_compaction", "persistence_io"])
async def test_http_bridge_late_anchor_classifies_input_and_persistence_failures(
    async_client, app_instance, monkeypatch, anchor_source, validation
):
    key = await _reasoning_key(
        async_client,
        allowed=["low"] if validation == "forbidden_effort" else None,
        enforced="low" if validation == "incompatible_compaction" else None,
    )
    service, upstream = await _bridge(async_client, app_instance, monkeypatch)
    original_submit = service._submit_http_bridge_request
    observed_sessions = []
    record_operation = AsyncMock(side_effect=AssertionError("Invalid request must not record an operation"))
    completed = SimpleNamespace(state="completed", event_spool_complete=True, response_id="resp_prior")

    async def submit_with_late_anchor(session, *, request_state, **kwargs):
        assert request_state.api_key is not None
        assert request_state.previous_response_id is None
        assert session.durable_session_id is not None
        observed_sessions.append(session)
        # A recovered hard turn can learn its prior response only from the
        # operation ledger, after the initial unanchored validation passed.
        request_state.hard_continuity_anchor = True
        with monkeypatch.context() as scoped:
            scoped.setattr(
                service._durable_bridge,
                "get_operation_by_fingerprint",
                AsyncMock(
                    return_value=completed if anchor_source == "hard_turn" else None,
                    side_effect=OSError("operation lookup unavailable") if validation == "persistence_io" else None,
                ),
            )
            scoped.setattr(service._durable_bridge, "get_operation", AsyncMock(return_value=None))
            scoped.setattr(service._durable_bridge, "get_latest_completed_operation", AsyncMock(return_value=completed))
            scoped.setattr(service._durable_bridge, "record_operation", record_operation)
            return await original_submit(session, request_state=request_state, **kwargs)

    monkeypatch.setattr(service, "_submit_http_bridge_request", submit_with_late_anchor)
    payload: dict[str, JsonValue] = {
        "model": "gpt-6-astra",
        "instructions": "",
        "input": "Continue",
        "prompt_cache_key": "astra-late",
    }
    if validation == "incompatible_compaction":
        payload["context_management"] = [{"type": "compaction", "compact_threshold": 200000}]
    try:
        response = await async_client.post("/v1/responses", json=payload, headers={"Authorization": f"Bearer {key}"})
        expected_status, expected_code = {
            "forbidden_effort": (403, "reasoning_effort_not_allowed"),
            "incompatible_compaction": (400, "invalid_request_error"),
            "persistence_io": (502, "bridge_continuity_persistence_failed"),
        }[validation]
        assert response.status_code == expected_status, response.text
        assert response.json()["error"]["code"] == expected_code
        assert len(observed_sessions) == 1
        session = observed_sessions[0]
        persistence_failed = validation == "persistence_io"
        assert session.closed is persistence_failed
        assert session.upstream_control.reconnect_requested is persistence_failed
        assert session.upstream_control.retire_after_drain is persistence_failed
        assert session.admission_waiter_count == 0
        assert upstream.sent_text == []
        record_operation.assert_not_awaited()
        if not persistence_failed:
            monkeypatch.setattr(service, "_submit_http_bridge_request", original_submit)
            retry = await async_client.post(
                "/v1/responses",
                json={
                    "model": "gpt-6-astra",
                    "instructions": "",
                    "input": "Valid next request",
                    "reasoning": {"effort": "low"},
                    "prompt_cache_key": "astra-late",
                },
                headers={"Authorization": f"Bearer {key}"},
            )
            assert retry.status_code == 200, retry.text
            assert len(upstream.sent_text) == 1
            assert not session.closed
            assert session in service._http_bridge_sessions.values()
    finally:
        await _close_bridge(service)
