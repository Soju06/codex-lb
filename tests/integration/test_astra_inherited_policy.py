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


async def test_previous_response_omitted_effort_allowed_list_matches_fresh_request(async_client, monkeypatch) -> None:
    await _import_account(async_client, "astra-inherited-omitted", "astra-inherited-omitted@example.com")
    key = await _reasoning_key(async_client, allowed=["low"])
    forwarded = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_astra_inherited_omitted")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
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

    assert response.status_code == 200, response.text
    assert len(forwarded) == 1
    assert forwarded[0]["input"] == [{"role": "user", "content": "Continue"}]


async def test_non_astra_direct_http_previous_response_body_is_not_trimmed(async_client, monkeypatch) -> None:
    await _import_account(async_client, "non-astra-trim", "non-astra-trim@example.com")
    input_items = [
        {"id": "rs_replay", "type": "reasoning", "summary": []},
        {
            "id": "msg_replay",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior"}],
        },
        {
            "id": "fc_replay",
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]
    forwarded = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_non_astra_trim")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    for extra in ({}, {"stream": False}):
        forwarded.clear()
        response = await async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-5.6-terra",
                "instructions": "",
                "previous_response_id": "resp_prior",
                "input": input_items,
                **extra,
            },
        )
        assert response.status_code == 200, response.text
        assert len(forwarded) == 1
        assert forwarded[0]["input"] == input_items
