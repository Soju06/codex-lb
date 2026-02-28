from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import update

import app.modules.proxy.service as proxy_module
from app.core.auth.refresh import RefreshError
from app.core.errors import openai_error
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.db.models import ApiKeyLimit
from app.db.session import SessionLocal

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


def _make_upstream_model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus"}),
        raw={},
    )


@pytest.mark.asyncio
async def test_backend_transcribe_forwards_file_and_prompt(async_client, monkeypatch):
    await _import_account(async_client, "acc_transcribe_backend", "backend-transcribe@example.com")

    captured: dict[str, object] = {}

    async def fake_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        captured["audio_bytes"] = audio_bytes
        captured["filename"] = filename
        captured["content_type"] = content_type
        captured["prompt"] = prompt
        captured["access_token"] = access_token
        captured["account_id"] = account_id
        return {"text": "hello from backend"}

    monkeypatch.setattr(proxy_module, "core_transcribe_audio", fake_transcribe)

    response = await async_client.post(
        "/backend-api/transcribe",
        data={"prompt": "speaker says hello"},
        files={"file": ("sample.wav", b"\x01\x02\x03", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "hello from backend"
    assert captured["audio_bytes"] == b"\x01\x02\x03"
    assert captured["filename"] == "sample.wav"
    assert captured["content_type"] == "audio/wav"
    assert captured["prompt"] == "speaker says hello"
    assert captured["access_token"] == "access-token"
    assert captured["account_id"] == "acc_transcribe_backend"


@pytest.mark.asyncio
async def test_v1_audio_transcriptions_rejects_unsupported_model(async_client):
    response = await async_client.post(
        "/v1/audio/transcriptions",
        data={"model": "gpt-4o-mini"},
        files={"file": ("sample.wav", b"\x00\x01", "audio/wav")},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request_error"
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["param"] == "model"


@pytest.mark.asyncio
async def test_v1_audio_transcriptions_forwards_prompt(async_client, monkeypatch):
    await _import_account(async_client, "acc_transcribe_v1", "v1-transcribe@example.com")
    captured: dict[str, object] = {}

    async def fake_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        captured["audio_bytes"] = audio_bytes
        captured["prompt"] = prompt
        captured["account_id"] = account_id
        return {"text": "hello from v1"}

    monkeypatch.setattr(proxy_module, "core_transcribe_audio", fake_transcribe)

    response = await async_client.post(
        "/v1/audio/transcriptions",
        data={"model": "gpt-4o-transcribe", "prompt": "domain context"},
        files={"file": ("voice.wav", b"\x0a\x0b", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "hello from v1"
    assert captured["audio_bytes"] == b"\x0a\x0b"
    assert captured["prompt"] == "domain context"
    assert captured["account_id"] == "acc_transcribe_v1"


@pytest.mark.asyncio
async def test_backend_transcribe_retry_uses_refreshed_account_id(async_client, monkeypatch):
    await _import_account(async_client, "acc_transcribe_retry_old", "retry-transcribe@example.com")
    captured_account_ids: list[str | None] = []

    async def fake_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        captured_account_ids.append(account_id)
        if len(captured_account_ids) == 1:
            raise proxy_module.ProxyResponseError(
                401,
                openai_error("invalid_api_key", "token expired"),
            )
        return {"text": "retried"}

    async def fake_ensure_fresh(self, account, force: bool = False):
        if force:
            account.chatgpt_account_id = "acc_transcribe_retry_new"
        return account

    monkeypatch.setattr(proxy_module, "core_transcribe_audio", fake_transcribe)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/transcribe",
        files={"file": ("sample.wav", b"\x03\x04", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "retried"
    assert captured_account_ids == ["acc_transcribe_retry_old", "acc_transcribe_retry_new"]


@pytest.mark.asyncio
async def test_backend_transcribe_initial_refresh_failure_returns_handled_error(async_client, monkeypatch):
    await _import_account(async_client, "acc_transcribe_refresh_fail", "refresh-fail-transcribe@example.com")
    transcribe_calls = 0

    async def fake_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        nonlocal transcribe_calls
        transcribe_calls += 1
        return {"text": "unexpected"}

    async def fake_ensure_fresh(self, account, force: bool = False):
        if not force:
            raise RefreshError(
                code="invalid_grant",
                message="refresh failed",
                is_permanent=False,
            )
        return account

    monkeypatch.setattr(proxy_module, "core_transcribe_audio", fake_transcribe)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/transcribe",
        files={"file": ("sample.wav", b"\x03\x04", "audio/wav")},
    )
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"]["code"] == "invalid_api_key"
    assert payload["error"]["type"] == "invalid_request_error"
    assert transcribe_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/transcribe", "/v1/audio/transcriptions"])
async def test_transcription_routes_require_api_key_when_enabled(async_client, endpoint):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    data = {"model": "gpt-4o-transcribe"} if endpoint == "/v1/audio/transcriptions" else {}
    response = await async_client.post(
        endpoint,
        data=data,
        files={"file": ("sample.wav", b"\x00\x01\x02", "audio/wav")},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/transcribe", "/v1/audio/transcriptions"])
async def test_transcription_model_restriction_uses_fixed_model(async_client, endpoint):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={"name": "transcribe-restricted", "allowedModels": ["gpt-5.1"]},
    )
    assert created.status_code == 200
    key = created.json()["key"]

    await _import_account(async_client, "acc_transcribe_restricted", "restricted-transcribe@example.com")

    data = {"model": "gpt-4o-transcribe"} if endpoint == "/v1/audio/transcriptions" else {}
    response = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        data=data,
        files={"file": ("sample.wav", b"\xaa\xbb", "audio/wav")},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"


@pytest.mark.asyncio
async def test_transcription_model_scoped_limit_applies(async_client):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "transcribe-limit",
            "limits": [
                {
                    "limitType": "total_tokens",
                    "limitWindow": "weekly",
                    "maxValue": 1,
                    "modelFilter": "gpt-4o-transcribe",
                }
            ],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]
    key_id = created.json()["id"]

    async with SessionLocal() as session:
        await session.execute(
            update(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id).values(current_value=1),
        )
        await session.commit()

    await _import_account(async_client, "acc_transcribe_limit", "limit-transcribe@example.com")

    response = await async_client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        data={"model": "gpt-4o-transcribe"},
        files={"file": ("sample.wav", b"\xdd\xee", "audio/wav")},
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_transcription_routing_ignores_model_registry_filter(async_client, monkeypatch):
    await _import_account(async_client, "acc_transcribe_registry", "registry-transcribe@example.com")
    registry = get_model_registry()
    registry.update({"plus": [_make_upstream_model("gpt-5.1")]})

    async def fake_transcribe(
        audio_bytes: bytes,
        *,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        access_token: str,
        account_id: str | None,
        base_url=None,
        session=None,
    ):
        return {"text": "registry bypass works"}

    monkeypatch.setattr(proxy_module, "core_transcribe_audio", fake_transcribe)

    response = await async_client.post(
        "/v1/audio/transcriptions",
        data={"model": "gpt-4o-transcribe"},
        files={"file": ("sample.wav", b"\x99\x88", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "registry bypass works"
