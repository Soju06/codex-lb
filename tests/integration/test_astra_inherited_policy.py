from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module
from tests.integration.test_openai_compat_features import _completed_event, _import_account

pytestmark = pytest.mark.integration


async def _reasoning_key(async_client, *, allowed=None, enforced=None):
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
    body = {"name": "astra-inherited-policy"}
    if allowed is not None:
        body["allowedReasoningEfforts"] = allowed
    if enforced is not None:
        body["enforcedReasoningEffort"] = enforced
    created = await async_client.post("/api/api-keys/", json=body)
    assert created.status_code == 200
    return created.json()["key"]


async def test_previous_response_enforced_effort_is_reset_before_upstream(async_client, monkeypatch) -> None:
    await _import_account(async_client, "astra-inherited", "astra-inherited@example.com")
    key = await _reasoning_key(async_client, enforced="low")
    forwarded = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_astra_inherited")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-6-astra",
            "instructions": "",
            "previous_response_id": "resp_prior_high",
            "input": [{"role": "user", "content": "Continue"}],
        },
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    assert forwarded[0]["input"][0] == {
        "type": "configuration_update",
        "reasoning": {"effort": "low"},
    }


async def test_previous_response_omitted_effort_must_allow_astra_default(async_client, monkeypatch) -> None:
    await _import_account(async_client, "astra-inherited-denied", "astra-inherited-denied@example.com")
    key = await _reasoning_key(async_client, allowed=["low"])

    async def fail_upstream(*args, **kwargs):
        raise AssertionError("Disallowed inherited-effort reset reached upstream")
        yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_upstream)
    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-6-astra",
            "instructions": "",
            "previous_response_id": "resp_prior_unknown",
            "input": [{"role": "user", "content": "Continue"}],
        },
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reasoning_effort_not_allowed"
