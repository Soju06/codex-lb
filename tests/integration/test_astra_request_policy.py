from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module
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
@pytest.mark.parametrize("policy", [{"allowedReasoningEfforts": ["low"]}, {"enforcedReasoningEffort": "low"}])
async def test_astra_history_update_cannot_override_key_policy(async_client, monkeypatch, endpoint, policy):
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

    async def fail_upstream(*args, **kwargs):
        raise AssertionError("Invalid configuration update reached upstream")
        yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_upstream)
    response = await async_client.post(
        endpoint,
        json=_payload(),
        headers={
            "Authorization": f"Bearer {created.json()['key']}",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reasoning_effort_not_allowed"


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
        {"reasoning": {"effort": "none"}},
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
@pytest.mark.parametrize("policy", [{"allowedReasoningEfforts": ["low"]}, {"enforcedReasoningEffort": "low"}])
async def test_astra_anchored_continuation_resets_inherited_reasoning(async_client, monkeypatch, endpoint, policy):
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
    created = await async_client.post("/api/api-keys/", json={"name": "astra-anchor-policy", **policy})
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
