from __future__ import annotations

import json

import pytest

import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,  # noqa: F401
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize(
    ("marker", "candidate_count", "succeeds"),
    [
        ("turn_client_unregistered", 1, True),
        ("http_turn_client_unregistered", 1, True),
        (None, 1, True),
        ("turn_client_unregistered", 0, False),
        ("turn_client_unregistered", 2, False),
        ("http_turn_client_unregistered", 0, False),
        ("http_turn_client_unregistered", 2, False),
        ("", 1, False),
        ("   ", 1, False),
        ("opaque-client-state", 1, False),
    ],
)
async def test_owner_miss_marker_compatibility_through_http_bridge(
    async_client, monkeypatch, path, marker, candidate_count, succeeds
):
    _install_bridge_settings(monkeypatch, enabled=True)
    # A second global account proves cardinality is scoped to the API key.
    account_ids = [
        await _import_account(async_client, f"marker_account_{index}", f"marker-{index}@example.com")
        for index in range(2 if candidate_count else 0)
    ]
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200
    key = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "marker-scope",
            "accountAssignmentScopeEnabled": True,
            "assignedAccountIds": account_ids[:candidate_count],
        },
    )
    assert key.status_code == 200
    assert key.json()["accountAssignmentScopeEnabled"] is bool(candidate_count)
    assert key.json()["assignedAccountIds"] == account_ids[:candidate_count]

    upstream = _FakeBridgeUpstreamWebSocket()
    connected_accounts: list[str] = []

    async def select_account(self, deadline, **kwargs):
        assert kwargs.get("preferred_account_id") in (None, account_ids[0])
        assert kwargs["api_key"].assigned_account_ids == account_ids[:candidate_count]
        return AccountSelection(account=await _get_account(account_ids[0]), error_message=None, error_code=None)

    async def fresh(self, account, *, force=False, timeout_seconds):
        return account

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        connected_accounts.append(account_id_header)
        return upstream

    async def reject_legacy(*args, **kwargs):
        raise AssertionError("HTTP bridge request escaped to legacy transport")

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", select_account)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    monkeypatch.setattr(proxy_module, "core_stream_responses", reject_legacy)
    headers = {"Authorization": f"Bearer {key.json()['key']}"}
    if marker is not None:
        headers["x-codex-turn-state"] = marker
    response = await async_client.post(
        path,
        headers=headers,
        json={
            "model": "gpt-5.1",
            "instructions": "Return OK.",
            "input": "Continue.",
            "previous_response_id": "resp_owner_not_recorded_locally",
            "stream": True,
        },
    )
    if succeeds:
        assert response.status_code == 200, response.text
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line[6:] != "[DONE]"
        ]
        assert events[-1]["type"] == "response.completed"
        assert events[-1]["response"]["id"] == "resp_bridge_1"
        assert connected_accounts == ["marker_account_0"]
        assert len(upstream.sent_text) == 1
        assert json.loads(upstream.sent_text[0])["previous_response_id"] == "resp_owner_not_recorded_locally"
    else:
        assert response.status_code == 502, response.text
        expected_code = (
            "turn_state_owner_unavailable" if marker == "opaque-client-state" else "previous_response_owner_unavailable"
        )
        assert response.json()["error"]["code"] == expected_code
        assert connected_accounts == []
        assert upstream.sent_text == []
