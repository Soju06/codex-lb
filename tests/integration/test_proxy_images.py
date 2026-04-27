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
from typing import Any, cast

import pytest

import app.modules.proxy.service as proxy_module
from app.core.config.settings import Settings
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
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
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
async def test_images_generations_unsupported_model_returns_400(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "a red circle"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


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
async def test_images_generations_returns_envelope_on_success(async_client, monkeypatch):
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
async def test_images_generations_failed_image_returns_5xx(async_client, monkeypatch):
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

    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "blocked"},
    )

    assert response.status_code in (400, 502)
    body = response.json()
    assert body["error"]["code"] in {"content_policy_violation", "image_generation_failed"}


# ---------------------------------------------------------------------------
# /v1/images/edits
# ---------------------------------------------------------------------------


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
async def test_images_edits_unsupported_model(async_client):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "dall-e-3",
            "prompt": "hi",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


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
