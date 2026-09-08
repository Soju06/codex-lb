from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.service as proxy_module
from tests.integration.test_astra_websocket_owner_policy import _continuation
from tests.integration.test_astra_websocket_owner_policy import (
    source_and_subscription_owner as source_and_subscription_owner,
)
from tests.integration.test_openai_compat_features import _completed_event, _import_account

pytestmark = pytest.mark.integration


def _payload(effort="high"):
    return {
        "model": "gpt-6-astra",
        "instructions": "",
        "reasoning": {"effort": "low"},
        "input": [
            {"type": "configuration_update", "reasoning": {"effort": effort}},
            {"role": "user", "content": "Continue"},
        ],
    }


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
async def test_astra_valid_update_reaches_subscription_without_rewriting_prefix(async_client, monkeypatch, endpoint):
    await _import_account(async_client, "astra-policy", "astra-policy@example.com")
    forwarded = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_astra_policy")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    response = await async_client.post(endpoint, json=_payload())
    assert response.status_code == 200
    assert len(forwarded) == 1
    assert forwarded[0]["reasoning"] == {"effort": "low"}
    assert forwarded[0]["input"][0] == _payload()["input"][0]


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize(
    ("effort", "policy", "expected_status"),
    [
        pytest.param("none", {}, 200, id="none-unrestricted"),
        pytest.param("disabled", {}, 200, id="disabled-unrestricted"),
        pytest.param("invalid", {}, 200, id="invalid-unrestricted"),
        pytest.param("none", {"allowedReasoningEfforts": ["low"]}, 403, id="none-allowed"),
        pytest.param("disabled", {"allowedReasoningEfforts": ["low"]}, 403, id="disabled-allowed"),
        pytest.param("invalid", {"allowedReasoningEfforts": ["low"]}, 403, id="invalid-allowed"),
        pytest.param("none", {"enforcedReasoningEffort": "low"}, 403, id="none-enforced"),
        pytest.param("disabled", {"enforcedReasoningEffort": "low"}, 403, id="disabled-enforced"),
        pytest.param("invalid", {"enforcedReasoningEffort": "low"}, 403, id="invalid-enforced"),
        pytest.param("low", {"allowedReasoningEfforts": ["low"]}, 200, id="low-allowed"),
        pytest.param("low", {"enforcedReasoningEffort": "low"}, 200, id="low-enforced"),
        pytest.param("high", {"allowedReasoningEfforts": ["low"]}, 403, id="high-allowed"),
        pytest.param("high", {"enforcedReasoningEffort": "low"}, 403, id="high-enforced"),
    ],
)
async def test_astra_update_schema_and_key_policy_errors(
    async_client, monkeypatch, endpoint, effort, policy, expected_status
):
    await _import_account(async_client, "astra-error-policy", "astra-error-policy@example.com")
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200
    created = await async_client.post("/api/api-keys/", json={"name": "astra-history-policy", **policy})
    assert created.status_code == 200
    forwarded = []

    async def fake_stream(payload, *args, **kwargs):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_astra_error_policy")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    response = await async_client.post(
        endpoint,
        json=_payload(effort),
        headers={
            "Authorization": f"Bearer {created.json()['key']}",
        },
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 200:
        assert len(forwarded) == 1
        assert forwarded[0]["input"][0] == _payload(effort)["input"][0]
    else:
        assert forwarded == []
        error = response.json()["error"]
        assert error["code"] == ("invalid_request_error" if expected_status == 400 else "reasoning_effort_not_allowed")
        assert error["type"] == ("invalid_request_error" if expected_status == 400 else "permission_error")
        assert error["param"] == "input.0.reasoning.effort"


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize(
    ("update", "policy"),
    [
        pytest.param({"type": "configuration_update", "reasoning": {"effort": 3}}, {}, id="non-string-unrestricted"),
        pytest.param(
            {"type": "configuration_update", "reasoning": {"effort": 3}},
            {"allowedReasoningEfforts": ["low"]},
            id="non-string-allowed",
        ),
        pytest.param(
            {"type": "configuration_update", "reasoning": {"effort": 3}},
            {"enforcedReasoningEffort": "low"},
            id="non-string-enforced",
        ),
    ],
)
def test_astra_invalid_update_with_subscription_owner_returns_400(
    source_and_subscription_owner, monkeypatch, endpoint, update, policy
):
    client, key, _ = source_and_subscription_owner
    if policy:
        updated = client.patch("/api/api-keys/" + key["id"], json=policy)
        assert updated.status_code == 200
    connect = AsyncMock(side_effect=AssertionError("Invalid subscription update reached upstream connection"))
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    payload = _continuation({"input": [update, {"role": "user", "content": "Continue"}]})

    with client.websocket_connect(endpoint, headers={"Authorization": "Bearer " + key["key"]}) as ws:
        ws.send_json(payload)
        event = ws.receive_json()

    assert event["type"] == "error"
    assert event["status"] == 400, event
    assert event["error"]["code"] == "invalid_request_error"
    assert event["error"]["type"] == "invalid_request_error"
    assert event["error"]["param"] == "input.0.reasoning.effort"
    connect.assert_not_awaited()


@pytest.mark.parametrize(
    "endpoint",
    [
        "/v1/responses/compact",
        "/backend-api/codex/responses/compact",
    ],
)
async def test_astra_compact_rejects_configuration_update_with_openai_error(async_client, monkeypatch, endpoint):
    async def fail_upstream(*args, **kwargs):
        raise AssertionError("Invalid compact request reached upstream")

    monkeypatch.setattr(proxy_module, "core_compact_responses", fail_upstream)
    response = await async_client.post(endpoint, json=_payload())
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.json()["error"]["param"] == "input"


@pytest.mark.parametrize(
    "extra",
    [
        {"top_logprobs": 3},
        {"truncation": "auto"},
        {"context_management": [{"type": "compaction", "compact_threshold": 200000}]},
    ],
)
async def test_astra_invalid_controls_return_400_before_upstream(async_client, monkeypatch, extra):
    async def fail_upstream(*args, **kwargs):
        raise AssertionError("Invalid Astra request reached upstream")
        yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_upstream)
    response = await async_client.post("/v1/responses", json={**_payload(), **extra})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
async def test_astra_explicit_compaction_with_updates_stays_on_responses(async_client, monkeypatch, endpoint):
    await _import_account(async_client, "astra-compact", "astra-compact@example.com")
    forwarded = []

    async def fake_stream(payload, *args, **kwargs):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_astra_compaction")

    async def fail_compact(*args, **kwargs):
        raise AssertionError("Explicit Astra compaction was converted to standalone compact")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fail_compact)
    payload = _payload("ultra")
    payload["input"].append({"type": "compaction_trigger"})
    response = await async_client.post(endpoint, json=payload)
    assert response.status_code == 200
    assert len(forwarded) == 1
    assert forwarded[0]["input"][0]["reasoning"]["effort"] == "max"
    assert forwarded[0]["input"][-1] == {"type": "compaction_trigger"}


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
async def test_astra_anchored_continuation_resets_inherited_reasoning(async_client, monkeypatch, endpoint):
    await _import_account(async_client, "astra-anchor-policy", "astra-anchor@example.com")
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200
    created = await async_client.post(
        "/api/api-keys/", json={"name": "astra-anchor-policy", "enforcedReasoningEffort": "low"}
    )
    assert created.status_code == 200
    forwarded = []

    async def fake_stream(payload, *args, **kwargs):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_astra_anchor_policy")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    response = await async_client.post(
        endpoint,
        json={
            "model": "gpt-6-astra",
            "instructions": "",
            "previous_response_id": "resp_inherited_high",
            "reasoning": {"effort": "low"},
            "input": [{"role": "user", "content": "Continue"}],
        },
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert response.status_code == 200, response.text
    assert len(forwarded) == 1
    assert forwarded[0]["previous_response_id"] == "resp_inherited_high"
    assert forwarded[0]["reasoning"]["effort"] == "low"
    assert forwarded[0]["input"][0] == {"type": "configuration_update", "reasoning": {"effort": "low"}}
    assert len(forwarded[0]["input"]) == 2


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
async def test_astra_allowed_list_continuation_does_not_prepend_explicit_effort(async_client, monkeypatch, endpoint):
    await _import_account(async_client, "astra-allowed-anchor", "astra-allowed-anchor@example.com")
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200
    created = await async_client.post(
        "/api/api-keys/", json={"name": "astra-allowed-anchor", "allowedReasoningEfforts": ["low"]}
    )
    assert created.status_code == 200
    forwarded = []

    async def fake_stream(payload, *args, **kwargs):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_astra_allowed_anchor")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    response = await async_client.post(
        endpoint,
        json={
            "model": "gpt-6-astra",
            "instructions": "",
            "previous_response_id": "resp_inherited_high",
            "reasoning": {"effort": "low"},
            "input": [{"role": "user", "content": "Continue"}],
        },
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert response.status_code == 200, response.text
    assert len(forwarded) == 1
    assert forwarded[0]["previous_response_id"] == "resp_inherited_high"
    assert forwarded[0]["reasoning"]["effort"] == "low"
    assert forwarded[0]["input"] == [{"role": "user", "content": "Continue"}]
