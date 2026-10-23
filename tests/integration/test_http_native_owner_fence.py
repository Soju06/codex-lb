from __future__ import annotations

from copy import deepcopy

import pytest

import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import streaming as bridge_streaming
from tests.integration.test_http_native_anchor_recovery import NativeToolUpstream, native_output
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,  # noqa: F401
    _collect_sse_events,
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


@pytest.mark.parametrize("route", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("quarantined", [False, True])
@pytest.mark.asyncio
async def test_native_full_resend_does_not_fallback_from_unavailable_owner(
    async_client, app_instance, monkeypatch, route, quarantined
) -> None:
    """An eligible alternate must not receive opaque history proved for another owner."""
    _install_bridge_settings(monkeypatch, enabled=True)
    owner_id = await _import_account(async_client, "native_fenced_owner", "native-fenced-owner@example.com")
    owner = await _get_account(owner_id)
    output = native_output(with_commentary=True)
    first = NativeToolUpstream(output)
    alternate_upstream = _FakeBridgeUpstreamWebSocket("resp_alternate_control")
    connected_accounts: list[str] = []

    async def fresh(self, account, **kwargs):
        return account

    async def connect(headers, access_token, account_id_header, **kwargs):
        connected_accounts.append(account_id_header)
        return first if len(connected_accounts) == 1 else alternate_upstream

    # Account selection and owner fallback remain real. Only transport and
    # token refresh are replaced, so a forbidden alternate selection is visible.
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    history = [{"role": "user", "content": "Keep all context and run the check."}]
    body = {
        "model": "gpt-5.1",
        "input": history,
        "instructions": "Preserve the conversation.",
        "prompt_cache_key": "native-owner-fence-cache",
        "stream": True,
    }
    headers = {"x-codex-session-id": "native-owner-fence-session"}
    events = await _collect_sse_events(async_client, route, json_body=body, headers=headers)
    assert events[-1]["response"]["id"] == "resp_native_anchor"
    service = get_proxy_service_for_app(app_instance)
    original_session = next(iter(service._http_bridge_sessions.values()))
    await service._close_http_bridge_session(original_session)

    alternate_id = await _import_account(async_client, "native_fenced_alternate", "native-fenced-alt@example.com")
    alternate = await _get_account(alternate_id)
    paused = await async_client.post(f"/api/accounts/{owner_id}/pause")
    assert paused.status_code == 200

    # Positive control: an unrelated request can use the alternate through the
    # same real selector. The negative result cannot be explained by an empty pool.
    control_events = await _collect_sse_events(
        async_client,
        route,
        json_body={"model": "gpt-5.1", "input": "A separate new conversation.", "stream": True},
        headers={"x-codex-session-id": "alternate-positive-control"},
    )
    assert control_events[-1]["type"] == "response.completed"
    assert connected_accounts == [owner.chatgpt_account_id, alternate.chatgpt_account_id]
    alternate_sends = len(alternate_upstream.sent_text)

    if quarantined:
        monkeypatch.setattr(bridge_streaming, "_http_bridge_session_key_quarantined", lambda *_args: True)

    full_history = [
        *history,
        *deepcopy(output),
        {
            "type": "custom_tool_call_output",
            "call_id": "call_native",
            "output": "The check completed.",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_native", "create_time": 1792706041.0},
        },
    ]
    lookup = await service._durable_bridge.lookup_request_targets(
        session_key_kind=original_session.key.affinity_kind,
        session_key_value=original_session.key.affinity_key,
        api_key_id=None,
        turn_state=None,
        session_header=headers["x-codex-session-id"],
        previous_response_id=None,
    )
    # This exercises the newly accepted owner-bound shape; an already-invalid
    # history refusing fallback would not establish the ownership guarantee.
    proof = bridge_streaming._verify_durable_full_resend(
        proxy_module.ResponsesRequest.model_validate({**body, "input": full_history}), lookup
    )
    assert proof is not None
    response = await async_client.post(route, json={**body, "input": full_history}, headers=headers)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "previous_response_owner_unavailable"
    assert connected_accounts == [owner.chatgpt_account_id, alternate.chatgpt_account_id]
    assert len(alternate_upstream.sent_text) == alternate_sends
