from __future__ import annotations

import json
from typing import cast

import pytest

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module

pytestmark = pytest.mark.integration


def _install_usage_limited_selection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    error_code: str | None = "usage_limit_reached",
) -> None:
    async def fake_select_account(*_args, **_kwargs):
        return proxy_module.AccountSelection(
            account=None,
            error_message="No eligible subscription account is available",
            error_code=error_code,
            resets_at=1_700_003_600 if error_code == "usage_limit_reached" else None,
        )

    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.LoadBalancer.select_account",
        fake_select_account,
    )


async def _create_source(
    async_client,
    *,
    name: str,
    model: str,
    fallback: bool = False,
    fallback_model: str | None = None,
) -> str:
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": "http://127.0.0.1:9/v1",
            "apiKey": f"token-{name}",
            "supportsChatCompletions": False,
            "supportsResponses": True,
            "isSubscriptionFallback": fallback,
            "fallbackModel": fallback_model,
            "models": [
                {
                    "model": model,
                    "displayName": model,
                    "supportsStreaming": True,
                    "supportsTools": True,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _first_failed_event(text: str) -> dict[str, object]:
    for line in text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        event = json.loads(line[6:])
        if event.get("type") == "response.failed":
            return event
    raise AssertionError(f"No response.failed event in stream: {text!r}")


@pytest.mark.parametrize(
    ("path", "stream"),
    [
        ("/backend-api/codex/responses", True),
        ("/v1/responses", False),
    ],
)
@pytest.mark.asyncio
async def test_usage_exhaustion_routes_to_designated_model_source(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    stream: bool,
) -> None:
    _install_usage_limited_selection(monkeypatch)
    source_id = await _create_source(
        async_client,
        name="subscription-fallback",
        model="gpt-5.4",
        fallback=True,
    )
    captured: dict[str, object] = {}

    async def fake_source_response(
        request,
        payload,
        *,
        source,
        api_key,
        rate_limit_headers,
        reservation_override=None,
        reuse_reservation=False,
    ):
        del request, api_key, rate_limit_headers
        captured.update(
            source_id=source.id,
            model=payload.model,
            stream=payload.stream,
            reservation=reservation_override,
            reuse_reservation=reuse_reservation,
        )
        if reservation_override is not None:
            await proxy_api_module._release_reservation(reservation_override)
        return proxy_api_module.JSONResponse(
            content={
                "id": "resp_subscription_fallback",
                "object": "response",
                "status": "completed",
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_api_module, "_source_responses_response", fake_source_response)

    response = await async_client.post(
        path,
        json={"model": "gpt-5.4", "instructions": "hi", "input": "hello", "stream": stream},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_subscription_fallback"
    assert captured["source_id"] == source_id
    assert captured["model"] == "gpt-5.4"
    assert captured["stream"] is stream
    assert captured["reuse_reservation"] is True


@pytest.mark.asyncio
async def test_fallback_model_override_is_forwarded(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_usage_limited_selection(monkeypatch)
    await _create_source(
        async_client,
        name="override-fallback",
        model="external-coder",
        fallback=True,
        fallback_model="external-coder",
    )
    captured_model: str | None = None

    async def fake_source_response(
        request,
        payload,
        *,
        source,
        api_key,
        rate_limit_headers,
        reservation_override=None,
        reuse_reservation=False,
    ):
        nonlocal captured_model
        del request, source, api_key, rate_limit_headers, reuse_reservation
        captured_model = payload.model
        if reservation_override is not None:
            await proxy_api_module._release_reservation(reservation_override)
        return proxy_api_module.JSONResponse(
            content={"id": "resp_override", "object": "response", "status": "completed", "output": []}
        )

    monkeypatch.setattr(proxy_api_module, "_source_responses_response", fake_source_response)

    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": "hello", "stream": False},
    )

    assert response.status_code == 200
    assert captured_model == "external-coder"


@pytest.mark.asyncio
async def test_fallback_reuses_existing_api_key_reservation(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_usage_limited_selection(monkeypatch)
    await _create_source(
        async_client,
        name="reservation-fallback",
        model="gpt-5.4",
        fallback=True,
    )
    settings_response = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings_response.status_code == 200
    key_response = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "fallback-reservation-key",
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000}],
        },
    )
    assert key_response.status_code == 200
    api_key = key_response.json()["key"]

    original_enforce = proxy_api_module._enforce_request_limits
    enforce_calls = 0
    captured_reservation = None

    async def counting_enforce(*args, **kwargs):
        nonlocal enforce_calls
        enforce_calls += 1
        return await original_enforce(*args, **kwargs)

    async def fake_source_response(
        request,
        payload,
        *,
        source,
        api_key,
        rate_limit_headers,
        reservation_override=None,
        reuse_reservation=False,
    ):
        nonlocal captured_reservation
        del request, payload, source, api_key, rate_limit_headers
        assert reuse_reservation is True
        assert reservation_override is not None
        captured_reservation = reservation_override
        await proxy_api_module._release_reservation(reservation_override)
        return proxy_api_module.JSONResponse(
            content={
                "id": "resp_subscription_fallback_limited",
                "object": "response",
                "status": "completed",
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", counting_enforce)
    monkeypatch.setattr(proxy_api_module, "_source_responses_response", fake_source_response)

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "gpt-5.4", "input": "hello", "stream": False},
    )

    assert response.status_code == 200
    assert captured_reservation is not None
    assert enforce_calls == 1


@pytest.mark.asyncio
async def test_api_key_source_scope_excludes_unassigned_fallback(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_usage_limited_selection(monkeypatch)
    await _create_source(
        async_client,
        name="excluded-fallback",
        model="gpt-5.4",
        fallback=True,
    )
    assigned_source_id = await _create_source(
        async_client,
        name="assigned-other-source",
        model="other-model",
    )
    settings_response = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings_response.status_code == 200
    key_response = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "scoped-fallback-key",
            "assignedSourceIds": [assigned_source_id],
        },
    )
    assert key_response.status_code == 200
    api_key = key_response.json()["key"]

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unassigned fallback source must not be contacted")

    monkeypatch.setattr(proxy_api_module, "_source_responses_response", fail_if_called)

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "gpt-5.4", "input": "hello", "stream": False},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "usage_limit_reached"


@pytest.mark.asyncio
async def test_non_quota_selection_failure_does_not_use_fallback(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_usage_limited_selection(monkeypatch, error_code=None)
    await _create_source(
        async_client,
        name="unused-fallback",
        model="gpt-5.4",
        fallback=True,
    )

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("fallback must not be contacted for non-quota selection failures")

    monkeypatch.setattr(proxy_api_module, "_source_responses_response", fail_if_called)

    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": "hello", "stream": True},
    )

    assert response.status_code == 200
    failed = _first_failed_event(response.text)
    response_payload = cast(dict[str, object], failed["response"])
    error = cast(dict[str, object], response_payload["error"])
    assert error["code"] == "no_accounts"
