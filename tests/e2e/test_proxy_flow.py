from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_proxy_chat_completions_flow(
    client,
    monkeypatch,
    setup_dashboard_password,
    enable_api_key_auth,
    create_api_key,
    import_test_account,
):
    await setup_dashboard_password(client)
    await enable_api_key_auth(client)
    created = await create_api_key(
        client,
        name="e2e-proxy-key",
        limits=[
            {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
        ],
    )
    await import_test_account(
        client,
        account_id="acc_e2e_proxy",
        email="e2e-proxy@example.com",
    )

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_e2e_proxy","usage":'
            '{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {created['key']}"},
        json={
            "model": "gpt-5.2",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_e2e_proxy"
    assert payload["object"] == "chat.completion"
    assert payload["usage"]["total_tokens"] == 5

    listed = await client.get("/api/api-keys/")
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["id"] == created["id"])
    assert row["usageSummary"]["requestCount"] == 1
    assert row["usageSummary"]["totalTokens"] == 5
