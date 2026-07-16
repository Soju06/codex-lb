"""Integration tests for the OpenAI Images API compatibility surface.

These exercise ``POST /v1/images/generations`` and ``POST /v1/images/edits``
end-to-end with a fake upstream Responses stream. They mirror the patterns
from ``test_proxy_responses.py`` (account import + ``core_stream_responses``
monkeypatch) so we cover the full request -> account selection -> SSE
translation -> public response shape pipeline.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, cast

import pytest
from starlette.responses import JSONResponse

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.config.settings import Settings
from app.core.exceptions import ProxyModelNotAllowed, ProxyRateLimitError
from app.db.models import DashboardSettings

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> None:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200


async def _enable_api_key_auth(async_client) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert response.status_code == 200


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@pytest.fixture(autouse=True)
def _disable_http_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    app_settings = Settings(
        http_responses_session_bridge_enabled=False,
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        stream_idle_timeout_seconds=300.0,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
    )
    dashboard_settings = DashboardSettings(
        id=1,
        sticky_threads_enabled=False,
        upstream_stream_transport="auto",
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        openai_cache_affinity_max_age_seconds=300,
        import_without_overwrite=False,
        totp_required_on_login=False,
        api_key_auth_enabled=False,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        sticky_reallocation_budget_threshold_pct=95.0,
    )

    class _SettingsCache:
        async def get(self) -> DashboardSettings:
            return dashboard_settings

    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_module, "get_settings", lambda: app_settings)


# ---------------------------------------------------------------------------
# /v1/images/generations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_images_generations_unsupported_model_returns_400(async_client, caplog):
    with caplog.at_level(logging.INFO, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/generations",
            json={"model": "dall-e-3", "prompt": "a red circle"},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert (
        "images_route_complete route=generations model=invalid stream=false status=400 outcome=invalid_request"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_generations_model_policy_rejection_records_route_observability(
    async_client,
    monkeypatch,
    caplog,
):
    def reject_model_access(*args, **kwargs):
        del args, kwargs
        raise ProxyModelNotAllowed("This API key does not have access to model 'gpt-image-2'")

    monkeypatch.setattr(proxy_api_module, "validate_model_access", reject_model_access)

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/generations",
            json={"model": "gpt-image-2", "prompt": "a red circle"},
        )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["type"] == "permission_error"
    assert (
        "images_route_complete route=generations model=gpt-image-2 stream=false status=403 outcome=model_not_allowed"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_generations_quota_rejection_records_route_observability(async_client, monkeypatch, caplog):
    async def reject_request_limits(*args, **kwargs):
        del args, kwargs
        raise ProxyRateLimitError("API key quota exceeded")

    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", reject_request_limits)

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/generations",
            json={"model": "gpt-image-2", "prompt": "a red circle"},
        )

    assert response.status_code == 429
    assert (
        "images_route_complete route=generations model=gpt-image-2 stream=false status=429 outcome=rate_limited"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_generations_auth_rejection_records_route_observability(async_client, caplog):
    await _enable_api_key_auth(async_client)

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/generations",
            json={"model": "gpt-image-2", "prompt": "a red circle"},
        )

    assert response.status_code == 401
    message = "images_route_complete route=generations model=gpt-image-2 stream=false status=401 outcome=auth_error"
    assert message in caplog.text
    assert caplog.text.count(message) == 1


@pytest.mark.asyncio
async def test_backend_codex_images_generations_alias_uses_same_validation(async_client):
    response = await async_client.post(
        "/backend-api/codex/images/generations",
        json={"model": "dall-e-3", "prompt": "a red circle"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "model"


@pytest.mark.asyncio
async def test_backend_codex_images_generations_alias_auth_rejection_records_route_observability(async_client, caplog):
    await _enable_api_key_auth(async_client)

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/backend-api/codex/images/generations",
            json={"model": "gpt-image-2", "prompt": "a red circle"},
        )

    assert response.status_code == 401
    message = "images_route_complete route=generations model=gpt-image-2 stream=false status=401 outcome=auth_error"
    assert message in caplog.text
    assert caplog.text.count(message) == 1


@pytest.mark.asyncio
async def test_images_generations_trailing_slash_parity_between_v1_and_codex_alias(async_client):
    # Codex joins `base_url` + "images/generations" without a trailing slash,
    # so only the exact paths are handled. The trailing-slash variants fall
    # through to the SPA catch-all route and must fail identically (405 with
    # the OpenAI error envelope) on the canonical and alias surfaces.
    payload = {"model": "dall-e-3", "prompt": "a red circle"}
    v1_response = await async_client.post("/v1/images/generations/", json=payload)
    alias_response = await async_client.post("/backend-api/codex/images/generations/", json=payload)
    assert v1_response.status_code == 405
    assert alias_response.status_code == 405
    assert v1_response.json()["error"]["type"] == "invalid_request_error"
    assert alias_response.json() == v1_response.json()


@pytest.mark.asyncio
async def test_images_generations_rejects_transparent_background(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "a red circle",
            "background": "transparent",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "background"


@pytest.mark.asyncio
async def test_images_generations_rejects_invalid_size_for_gpt_image_2(async_client):
    # 1023x1024 is rejected because 1023 is not a multiple of 16.
    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "a red circle",
            "size": "1023x1024",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "size"


@pytest.mark.asyncio
async def test_images_generations_legacy_size_rejected(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "edit", "size": "2048x2048"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "size"


@pytest.mark.asyncio
async def test_images_generations_no_accounts_returns_5xx(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "a red circle"},
    )
    assert response.status_code in (502, 503)
    body = response.json()
    assert body["error"]["type"] in {"server_error", "invalid_request_error"}


@pytest.mark.asyncio
async def test_images_generations_returns_envelope_on_success(async_client, monkeypatch, caplog):
    await _import_account(async_client, "acc_images_basic", "img-basic@example.com")

    captured: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        captured["model"] = payload.model
        captured["tools"] = list(payload.tools)
        captured["instructions"] = payload.instructions
        captured["input"] = payload.input
        captured["account_id"] = account_id
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_basic",
                    "status": "completed",
                    "result": "b64-image-bytes",
                    "revised_prompt": "a clean red circle",
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                },
            }
        )
        yield _sse(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_images_basic",
                    "object": "response",
                    "status": "completed",
                    "tool_usage": {"image_gen": {"input_tokens": 7, "output_tokens": 13}},
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/generations",
            json={
                "model": "gpt-image-2",
                "prompt": "tiny red circle on white",
                "n": 1,
                "size": "1024x1024",
                "quality": "low",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "created" in body
    assert body["data"] == [{"b64_json": "b64-image-bytes", "revised_prompt": "a clean red circle"}]
    assert body["usage"] == {"input_tokens": 7, "output_tokens": 13, "total_tokens": 20}

    # The host model is hidden from clients but appears in the upstream call.
    assert captured["model"] == "gpt-5.5"
    tools = cast(list[Any], captured["tools"])
    image_tool = cast(dict[str, Any], tools[0])
    assert image_tool["type"] == "image_generation"
    assert image_tool["model"] == "gpt-image-2"
    assert image_tool["size"] == "1024x1024"
    assert image_tool["quality"] == "low"
    assert (
        "images_route_complete route=generations model=gpt-image-2 stream=false status=200 outcome=success"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_generations_streaming_emits_canonical_events(async_client, monkeypatch):
    await _import_account(async_client, "acc_images_stream", "img-stream@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse({"type": "response.created", "response": {"id": "resp_x"}})
        yield _sse({"type": "response.in_progress"})
        yield _sse({"type": "response.image_generation_call.in_progress"})
        yield _sse(
            {
                "type": "response.image_generation_call.partial_image",
                "partial_image_b64": "PART_0",
                "partial_image_index": 0,
                "size": "1024x1024",
                "quality": "low",
                "background": "auto",
                "output_format": "png",
                "output_index": 1,
            }
        )
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_stream",
                    "status": "completed",
                    "result": "FINAL_B64",
                    "revised_prompt": "neat",
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                },
            }
        )
        yield _sse(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_x",
                    "tool_usage": {"image_gen": {"input_tokens": 1, "output_tokens": 2}},
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    async with async_client.stream(
        "POST",
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "tiny red circle on white",
            "stream": True,
            "partial_images": 2,
            "size": "1024x1024",
            "quality": "low",
        },
    ) as resp:
        assert resp.status_code == 200
        body_lines = [line async for line in resp.aiter_lines()]

    payloads: list[dict[str, object]] = []
    last_done = False
    for line in body_lines:
        if not line:
            continue
        if line.strip() == "data: [DONE]":
            last_done = True
            continue
        if line.startswith("data: "):
            payloads.append(json.loads(line[len("data: ") :]))

    assert last_done is True
    types = [p["type"] for p in payloads]
    assert types == ["image_generation.partial_image", "image_generation.completed"]
    assert payloads[0]["b64_json"] == "PART_0"
    assert payloads[0]["partial_image_index"] == 0
    assert payloads[1]["b64_json"] == "FINAL_B64"
    assert payloads[1]["revised_prompt"] == "neat"


@pytest.mark.asyncio
async def test_images_generations_streaming_records_image_errors(async_client, monkeypatch, caplog):
    await _import_account(async_client, "acc_images_stream_error", "img-stream-error@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse({"type": "response.created", "response": {"id": "resp_stream_error"}})
        yield _sse(
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "content_policy_violation",
                        "message": "blocked",
                        "type": "invalid_request_error",
                    },
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.api"):
        async with async_client.stream(
            "POST",
            "/v1/images/generations",
            json={
                "model": "gpt-image-2",
                "prompt": "blocked",
                "stream": True,
                "size": "1024x1024",
                "quality": "low",
            },
        ) as resp:
            assert resp.status_code == 200
            body = await resp.aread()

    assert b"content_policy_violation" in body
    assert (
        "images_route_complete route=generations model=gpt-image-2 stream=true status=200 outcome=image_error"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_generations_streaming_records_post_prime_upstream_errors(async_client, monkeypatch, caplog):
    from app.core.clients.proxy import ProxyResponseError

    await _import_account(async_client, "acc_images_stream_late_error", "img-stream-late-error@example.com")

    async def fake_stream_responses(self, payload, headers, **kwargs):
        del self, payload, headers, kwargs
        yield _sse({"type": "response.created", "response": {"id": "resp_stream_late_error"}})
        raise ProxyResponseError(
            status_code=503,
            payload={"error": {"message": "late boom", "type": "server_error", "code": "upstream_error"}},
        )

    monkeypatch.setattr(proxy_module.ProxyService, "stream_responses", fake_stream_responses)

    with caplog.at_level(logging.INFO, logger="app.modules.proxy.api"):
        with pytest.raises(ProxyResponseError):
            async with async_client.stream(
                "POST",
                "/v1/images/generations",
                json={
                    "model": "gpt-image-2",
                    "prompt": "late failure",
                    "stream": True,
                    "size": "1024x1024",
                    "quality": "low",
                },
            ) as resp:
                assert resp.status_code == 200
                await resp.aread()

    assert (
        "images_route_complete route=generations model=gpt-image-2 stream=true status=200 outcome=upstream_error"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_generations_failed_image_returns_5xx(async_client, monkeypatch, caplog):
    await _import_account(async_client, "acc_images_failed", "img-failed@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "status": "failed",
                    "error": {
                        "code": "content_policy_violation",
                        "message": "blocked",
                        "type": "invalid_request_error",
                    },
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/generations",
            json={"model": "gpt-image-2", "prompt": "blocked"},
        )

    assert response.status_code in (400, 502)
    body = response.json()
    assert body["error"]["code"] in {"content_policy_violation", "image_generation_failed"}
    assert "images_route_complete route=generations model=gpt-image-2 stream=false" in caplog.text
    assert "outcome=image_error" in caplog.text


# ---------------------------------------------------------------------------
# /v1/images/edits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_codex_images_edits_requires_native_image_data_urls(async_client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/backend-api/codex/images/edits",
            json={"model": "gpt-image-2", "prompt": "make it green", "images": []},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "images"
    assert (
        "images_route_complete route=edits model=gpt-image-2 stream=false status=400 outcome=invalid_request"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_backend_codex_images_edits_alias_auth_rejection_records_route_observability(async_client, caplog):
    await _enable_api_key_auth(async_client)

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/backend-api/codex/images/edits",
            json={
                "model": "gpt-image-2",
                "prompt": "make it green",
                "images": [{"image_url": "data:image/png;base64,aGVsbG8="}],
            },
        )

    assert response.status_code == 401
    message = "images_route_complete route=edits model=gpt-image-2 stream=false status=401 outcome=auth_error"
    assert message in caplog.text
    assert caplog.text.count(message) == 1


@pytest.mark.asyncio
async def test_images_edits_trailing_slash_parity_between_v1_and_codex_alias(async_client):
    # Codex joins `base_url` + "images/edits" without a trailing slash, so
    # only the exact paths are handled. The trailing-slash variants fall
    # through to the SPA catch-all route and must fail identically (405 with
    # the OpenAI error envelope) on the canonical and alias surfaces.
    payload = {"model": "gpt-image-2", "prompt": "make it green", "images": []}
    v1_response = await async_client.post("/v1/images/edits/", json=payload)
    alias_response = await async_client.post("/backend-api/codex/images/edits/", json=payload)
    assert v1_response.status_code == 405
    assert alias_response.status_code == 405
    assert v1_response.json()["error"]["type"] == "invalid_request_error"
    assert alias_response.json() == v1_response.json()


@pytest.mark.asyncio
async def test_backend_codex_images_edits_invalid_utf8_returns_400(async_client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/backend-api/codex/images/edits",
            content=b"\xff",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "prompt"
    assert (
        "images_route_complete route=edits model=unknown stream=false status=400 outcome=invalid_request" in caplog.text
    )


@pytest.mark.asyncio
async def test_images_edits_basic_round_trip(async_client, monkeypatch):
    await _import_account(async_client, "acc_images_edit", "img-edit@example.com")

    captured: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        captured["input"] = payload.input
        captured["tools"] = list(payload.tools)
        captured["account_id"] = account_id
        yield _sse(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_edit",
                    "status": "completed",
                    "result": "EDITED_B64",
                    "revised_prompt": "edited",
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-1",
            "prompt": "make it green",
            "size": "1024x1024",
            "quality": "low",
        },
        files={
            "image": ("source.png", image_bytes, "image/png"),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [{"b64_json": "EDITED_B64", "revised_prompt": "edited"}]

    # Verify the upstream payload contained the image as an input_image data
    # URL alongside the prompt text.
    input_value = cast(list[Any], captured["input"])
    assert input_value
    first_message = cast(dict[str, Any], input_value[0])
    content = cast(list[Any], first_message["content"])
    text_part = cast(dict[str, Any], content[0])
    assert text_part["type"] == "input_text"
    assert text_part["text"] == "make it green"
    image_parts: list[dict[str, Any]] = [
        cast(dict[str, Any], p) for p in content if isinstance(p, dict) and p.get("type") == "input_image"
    ]
    assert len(image_parts) == 1
    image_url_value = cast(str, image_parts[0]["image_url"])
    assert image_url_value.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_backend_codex_images_edits_json_data_urls_round_trip(async_client, monkeypatch):
    await _import_account(async_client, "acc_images_edit_codex", "img-edit-codex@example.com")

    captured: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        captured["input"] = payload.input
        captured["tools"] = list(payload.tools)
        captured["account_id"] = account_id
        yield _sse(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_edit_codex",
                    "status": "completed",
                    "result": "EDITED_B64",
                    "revised_prompt": "edited",
                    "size": "1024x1024",
                    "quality": "auto",
                    "background": "auto",
                    "output_format": "png",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    image_url = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
    response = await async_client.post(
        "/backend-api/codex/images/edits",
        json={
            "model": "gpt-image-2",
            "prompt": "make it green",
            "images": [{"image_url": image_url}],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [{"b64_json": "EDITED_B64", "revised_prompt": "edited"}]
    input_items = cast(list[dict[str, Any]], captured["input"])
    content = cast(list[dict[str, Any]], input_items[0]["content"])
    assert content[1]["type"] == "input_image"

    # Verify the upstream payload contained the image as an input_image data
    # URL alongside the prompt text.
    input_value = cast(list[Any], captured["input"])
    assert input_value
    first_message = cast(dict[str, Any], input_value[0])
    content = cast(list[Any], first_message["content"])
    text_part = cast(dict[str, Any], content[0])
    assert text_part["type"] == "input_text"
    assert text_part["text"] == "make it green"
    image_parts: list[dict[str, Any]] = [
        cast(dict[str, Any], p) for p in content if isinstance(p, dict) and p.get("type") == "input_image"
    ]
    assert len(image_parts) == 1
    image_url_value = cast(str, image_parts[0]["image_url"])
    assert image_url_value.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_images_edits_passes_outer_started_at_after_file_reads(async_client, monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_proxy_images_edit_request(**kwargs):
        captured.update(kwargs)
        return JSONResponse({"ok": True})

    monkeypatch.setattr(proxy_api_module.time, "perf_counter", lambda: 123.456)
    monkeypatch.setattr(proxy_api_module, "_proxy_images_edit_request", fake_proxy_images_edit_request)

    image_bytes = b"\x89PNG\r\n\x1a\n"
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "hi",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )

    assert response.status_code == 200
    assert captured["started_at"] == 123.456
    assert captured["images"] == [(image_bytes, "image/png")]


@pytest.mark.asyncio
async def test_images_edits_input_fidelity_rejected_on_gpt_image_2(async_client):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "hi",
            "input_fidelity": "high",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "input_fidelity"


@pytest.mark.asyncio
async def test_images_edits_unsupported_model(async_client, caplog):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/edits",
            data={
                "model": "bad-model-for-observability-cardinality",
                "prompt": "hi",
            },
            files={"image": ("source.png", image_bytes, "image/png")},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert (
        "images_route_complete route=edits model=invalid stream=false status=400 outcome=invalid_request" in caplog.text
    )


@pytest.mark.asyncio
async def test_images_edits_missing_image_records_route_observability(async_client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/edits",
            data={
                "model": "gpt-image-2",
                "prompt": "hi",
            },
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "image"
    assert (
        "images_route_complete route=edits model=gpt-image-2 stream=false status=400 outcome=invalid_request"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_edits_validation_error_records_supplied_model(async_client, caplog):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/edits",
            data={"model": "gpt-image-2"},
            files={"image": ("source.png", image_bytes, "image/png")},
        )

    assert response.status_code == 400
    assert (
        "images_route_complete route=edits model=gpt-image-2 stream=false status=400 outcome=invalid_request"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_images_edits_quota_rejection_records_route_observability(async_client, monkeypatch, caplog):
    async def reject_request_limits(*args, **kwargs):
        del args, kwargs
        raise ProxyRateLimitError("API key quota exceeded")

    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", reject_request_limits)

    image_bytes = b"\x89PNG\r\n\x1a\n"
    with caplog.at_level(logging.WARNING, logger="app.modules.proxy.api"):
        response = await async_client.post(
            "/v1/images/edits",
            data={
                "model": "gpt-image-2",
                "prompt": "hi",
            },
            files={"image": ("source.png", image_bytes, "image/png")},
        )

    assert response.status_code == 429
    assert (
        "images_route_complete route=edits model=gpt-image-2 stream=false status=429 outcome=rate_limited"
        in caplog.text
    )


# ---------------------------------------------------------------------------
# /v1/images/variations is explicitly not supported.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_images_variations_returns_404(async_client):
    response = await async_client.post(
        "/v1/images/variations",
        json={"model": "gpt-image-2", "prompt": "no"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found_error"


# ---------------------------------------------------------------------------
# Defaults, n>1 rejection, and stream priming.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_images_generations_falls_back_to_default_model_when_omitted(async_client, monkeypatch):
    """Omitting ``model`` should fall back to ``settings.images_default_model``."""
    await _import_account(async_client, "acc_images_default", "img-default@example.com")

    captured: dict[str, Any] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, account_id, base_url, raise_for_status, kwargs
        captured["tools"] = list(payload.tools)
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_default",
                    "status": "completed",
                    "result": "AAAA",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_default"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={"prompt": "no model given", "size": "1024x1024", "quality": "low"},
    )
    assert response.status_code == 200, response.text
    image_tool = cast(dict[str, Any], cast(list[Any], captured["tools"])[0])
    # Default falls back to images_default_model = "gpt-image-2".
    assert image_tool["model"] == "gpt-image-2"


@pytest.mark.asyncio
async def test_images_generations_rejects_n_greater_than_one(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "x", "n": 2},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "n"
    assert body["error"]["code"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_generations_propagates_upstream_error_before_first_chunk(async_client, monkeypatch):
    """When the upstream stream raises before any chunk is emitted, we
    surface a structured OpenAI error envelope instead of a broken SSE
    body. This guards against the prime-before-StreamingResponse path in
    the route handler.
    """
    from app.core.clients.proxy import ProxyResponseError

    await _import_account(async_client, "acc_images_prime", "img-prime@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        if False:  # pragma: no cover - generator marker only
            yield ""
        raise ProxyResponseError(
            status_code=503,
            payload={"error": {"message": "boom", "type": "server_error", "code": "upstream_error"}},
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "x", "size": "1024x1024", "quality": "low"},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_images_edits_falls_back_to_default_model_when_omitted(async_client, monkeypatch):
    """Omitting ``model`` on multipart edits should fall back to
    ``settings.images_default_model`` instead of being rejected by the
    FastAPI form parser with a 422.
    """
    await _import_account(async_client, "acc_images_edit_default", "img-edit-default@example.com")

    captured: dict[str, Any] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, account_id, base_url, raise_for_status, kwargs
        captured["tools"] = list(payload.tools)
        yield _sse(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_edit_default",
                    "status": "completed",
                    "result": "DEFAULT_EDITED_B64",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_edit_default"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "prompt": "no model on edits",
            "size": "1024x1024",
            "quality": "low",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    image_tool = cast(dict[str, Any], cast(list[Any], captured["tools"])[0])
    assert image_tool["model"] == "gpt-image-2"


@pytest.mark.asyncio
async def test_images_edits_invalid_n_returns_openai_error(async_client):
    """A bad ``n`` form value must come back as an OpenAI-shaped 400, not
    a FastAPI 422 with the framework's default error envelope.
    """
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "x",
            "n": "abc",  # not a valid integer
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_edits_invalid_stream_returns_openai_error(async_client):
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "x",
            "stream": "yesplz",  # not a valid bool
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_generations_maps_content_policy_to_400(async_client, monkeypatch):
    """An upstream content policy violation must surface as HTTP 400, not
    the previous hard-coded 502, so clients get the canonical OpenAI
    status for client-originated failures.
    """
    await _import_account(async_client, "acc_images_cp", "img-cp@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_cp",
                    "status": "failed",
                    "error": {
                        "code": "content_policy_violation",
                        "message": "policy violation",
                        "type": "invalid_request_error",
                    },
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_cp"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "x", "size": "1024x1024", "quality": "low"},
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "content_policy_violation"


@pytest.mark.asyncio
async def test_images_generations_rejects_input_fidelity(async_client):
    """``input_fidelity`` is an edit-only parameter; generations requests
    must reject it deterministically at the API boundary instead of
    silently dropping it via ``extra=ignore``.
    """
    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-1.5",
            "prompt": "x",
            "size": "1024x1024",
            "input_fidelity": "high",
        },
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["param"] == "input_fidelity"
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_edits_accepts_image_brackets_form_key(async_client, monkeypatch):
    """OpenAI SDKs/HTTP clients commonly send multiple files under
    ``image[]`` instead of ``image``. Both keys must work for drop-in
    compatibility.
    """
    await _import_account(async_client, "acc_images_brackets", "img-brackets@example.com")

    captured: dict[str, Any] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, account_id, base_url, raise_for_status, kwargs
        captured["input"] = payload.input
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_brackets",
                    "status": "completed",
                    "result": "B64_BRACKETS",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_brackets"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "image[] form key test",
        },
        files={
            "image[]": ("source.png", image_bytes, "image/png"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # ``exclude_none=True`` drops missing revised_prompt from the public envelope.
    assert body["data"] == [{"b64_json": "B64_BRACKETS"}]
    # Confirm the file was actually picked up and forwarded to upstream.
    input_value = cast(list[Any], captured["input"])
    first_message = cast(dict[str, Any], input_value[0])
    content = cast(list[Any], first_message["content"])
    image_parts = [cast(dict[str, Any], p) for p in content if isinstance(p, dict) and p.get("type") == "input_image"]
    assert len(image_parts) == 1


@pytest.mark.asyncio
async def test_images_generations_succeeds_when_reservation_finalize_fails(async_client, monkeypatch):
    """A successful image generation must NOT 500 when the post-hoc
    API-key reservation finalize raises (e.g. transient DB failure).
    The accounting failure is swallowed and logged; the client still
    receives the image envelope.
    """
    await _import_account(async_client, "acc_images_finalize_fail", "img-fin-fail@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_finfail",
                    "status": "completed",
                    "result": "B64_FINFAIL",
                },
            }
        )
        yield _sse(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_finfail",
                    "tool_usage": {"image_gen": {"input_tokens": 4, "output_tokens": 5}},
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    # Patch finalize to blow up so we can confirm the route still 200s.
    from app.modules.api_keys.service import ApiKeysService

    async def fake_finalize(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("simulated DB failure during finalize")

    monkeypatch.setattr(ApiKeysService, "finalize_usage_reservation", fake_finalize)

    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "x",
            "size": "1024x1024",
            "quality": "low",
        },
    )
    # Even though finalize raised, the client still sees a 200 with the image.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [{"b64_json": "B64_FINFAIL"}]
