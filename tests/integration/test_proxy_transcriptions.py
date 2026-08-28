from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from tempfile import SpooledTemporaryFile

import pytest
import starlette.formparsers as starlette_formparsers
from httpx import AsyncByteStream
from sqlalchemy import update

import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_module
from app.core.auth.refresh import RefreshError
from app.core.errors import openai_error
from app.core.multipart import TRANSCRIPTION_MULTIPART_POLICY
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.db.models import ApiKeyLimit
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


class _CountingBody(AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.iterations = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterations += 1
        yield self.body


def _record_spools(monkeypatch: pytest.MonkeyPatch) -> list[SpooledTemporaryFile[bytes]]:
    original = starlette_formparsers.SpooledTemporaryFile
    spools: list[SpooledTemporaryFile[bytes]] = []

    def create_spool(*, max_size: int) -> SpooledTemporaryFile[bytes]:
        spool = original(max_size=max_size)
        spools.append(spool)
        return spool

    monkeypatch.setattr(starlette_formparsers, "SpooledTemporaryFile", create_spool)
    return spools


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
async def test_transcription_openapi_preserves_explicit_multipart_contract(app_instance) -> None:
    schemas = {
        "/backend-api/transcribe": {
            "type": "object",
            "title": "Body_backend_transcribe_backend_api_transcribe_post",
            "required": ["file"],
            "properties": {
                "file": {
                    "type": "string",
                    "contentMediaType": "application/octet-stream",
                    "title": "File",
                },
                "prompt": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "title": "Prompt",
                },
            },
        },
        "/v1/audio/transcriptions": {
            "type": "object",
            "title": "Body_v1_audio_transcriptions_v1_audio_transcriptions_post",
            "required": ["model", "file"],
            "properties": {
                "model": {"type": "string", "title": "Model"},
                "file": {
                    "type": "string",
                    "contentMediaType": "application/octet-stream",
                    "title": "File",
                },
                "prompt": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "title": "Prompt",
                },
            },
        },
    }

    openapi = app_instance.openapi()
    for path, expected_schema in schemas.items():
        request_body = openapi["paths"][path]["post"]["requestBody"]
        assert request_body["required"] is True
        assert request_body["content"]["multipart/form-data"]["schema"] == expected_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/transcribe", "/v1/audio/transcriptions"])
async def test_transcription_auth_rejection_does_not_consume_multipart_body(async_client, endpoint: str) -> None:
    await _enable_api_key_auth(async_client)
    body = _CountingBody(b"multipart body must remain unread")

    response = await async_client.post(
        endpoint,
        content=body,
        headers={"Content-Type": "multipart/form-data; boundary=unused"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert body.iterations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/transcribe", "/v1/audio/transcriptions"])
async def test_transcription_encoded_body_rejects_after_auth_without_consuming_body(
    async_client,
    endpoint: str,
) -> None:
    await _enable_api_key_auth(async_client)
    created = await async_client.post("/api/api-keys/", json={"name": f"encoded-{endpoint}"})
    assert created.status_code == 200
    body = _CountingBody(b"compressed bytes must remain unread")

    response = await async_client.post(
        endpoint,
        content=body,
        headers={
            "Authorization": f"Bearer {created.json()['key']}",
            "Content-Type": "multipart/form-data; boundary=unused",
            "Content-Encoding": "gzip",
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request_error"
    assert payload["error"]["type"] == "invalid_request_error"
    assert body.iterations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "expected_param"),
    [("/backend-api/transcribe", "file"), ("/v1/audio/transcriptions", "model")],
)
async def test_transcription_missing_content_type_retains_openai_validation(
    async_client,
    endpoint: str,
    expected_param: str,
) -> None:
    response = await async_client.post(endpoint, content=b"not multipart")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request_error"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == expected_param


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/transcribe", "/v1/audio/transcriptions"])
async def test_transcription_declared_body_limit_rejects_before_reservation_or_body_read(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    reservation_calls = 0

    async def unexpected_reservation(*args, **kwargs):
        nonlocal reservation_calls
        reservation_calls += 1
        raise AssertionError("multipart admission must precede usage reservation")

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", unexpected_reservation)
    body = _CountingBody(b"body is rejected from its declared length")
    response = await async_client.post(
        endpoint,
        content=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=unused",
            "Content-Length": str(TRANSCRIPTION_MULTIPART_POLICY.max_body_bytes + 1),
        },
    )

    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == "payload_too_large"
    assert payload["error"]["type"] == "invalid_request_error"
    assert body.iterations == 0
    assert reservation_calls == 0


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
async def test_backend_transcribe_exact_file_limit_closes_spool_before_reservation_and_service(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools = _record_spools(monkeypatch)
    payload = b"x" * TRANSCRIPTION_MULTIPART_POLICY.max_file_bytes
    events: list[str] = []

    async def fake_reservation(*args, **kwargs):
        del args, kwargs
        assert spools
        assert all(spool.closed for spool in spools)
        events.append("reservation")
        return None

    async def fake_transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers,
        api_key=None,
    ):
        del self, headers, api_key
        assert spools
        assert all(spool.closed for spool in spools)
        events.append("service")
        assert audio_bytes == payload
        assert filename == "exact.wav"
        assert content_type == "audio/wav"
        assert prompt == "exact boundary"
        return {"text": "exact upload accepted"}

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", fake_reservation)
    monkeypatch.setattr(proxy_module.ProxyService, "transcribe", fake_transcribe)

    response = await async_client.post(
        "/backend-api/transcribe",
        data={"prompt": "exact boundary"},
        files={"file": ("exact.wav", payload, "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "exact upload accepted"}
    assert events == ["reservation", "service"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/transcribe", "/v1/audio/transcriptions"])
async def test_transcription_file_limit_rejects_before_selection_reservation_or_upstream(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    calls: list[str] = []

    async def unexpected_call(*args, **kwargs):
        del args, kwargs
        calls.append("unexpected")
        raise AssertionError("oversized upload must not invoke route side effects")

    monkeypatch.setattr(proxy_api, "_select_audio_transcriptions_model_source", unexpected_call)
    monkeypatch.setattr(proxy_api, "_enforce_request_limits", unexpected_call)
    monkeypatch.setattr(proxy_module.ProxyService, "transcribe", unexpected_call)
    payload = b"x" * (TRANSCRIPTION_MULTIPART_POLICY.max_file_bytes + 1)
    data = {"model": "gpt-4o-transcribe"} if endpoint.startswith("/v1/") else None

    response = await async_client.post(
        endpoint,
        data=data,
        files={"file": ("oversized.wav", payload, "audio/wav")},
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "file"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/transcribe", "/v1/audio/transcriptions"])
@pytest.mark.parametrize("invalid_shape", ["missing_file", "extra_file"])
async def test_transcription_invalid_file_shape_has_no_route_side_effects(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    invalid_shape: str,
) -> None:
    calls: list[str] = []

    async def unexpected_call(*args, **kwargs):
        del args, kwargs
        calls.append("unexpected")
        raise AssertionError("invalid multipart shape must not invoke route side effects")

    monkeypatch.setattr(proxy_api, "_select_audio_transcriptions_model_source", unexpected_call)
    monkeypatch.setattr(proxy_api, "_enforce_request_limits", unexpected_call)
    monkeypatch.setattr(proxy_module.ProxyService, "transcribe", unexpected_call)
    fields = [("model", (None, "gpt-4o-transcribe"))] if endpoint.startswith("/v1/") else []
    if invalid_shape == "missing_file":
        fields.append(("prompt", (None, "missing audio")))
    else:
        fields.extend(
            [
                ("file", ("audio.wav", b"audio", "audio/wav")),
                ("extra", ("extra.wav", b"extra", "audio/wav")),
            ]
        )

    response = await async_client.post(endpoint, files=fields)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request_error"
    if invalid_shape == "missing_file":
        assert error["param"] == "file"
    assert calls == []


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

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
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
async def test_backend_transcribe_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_transcribe_invalidated_a", "transcribe-invalidated-a@example.com")
    await _import_account(async_client, "acc_transcribe_invalidated_b", "transcribe-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

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
        del audio_bytes, filename, content_type, prompt, headers, access_token, base_url, session
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise proxy_module.ProxyResponseError(
                401,
                openai_error("invalid_api_key", "token invalidated"),
            )
        return {"text": "recovered"}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_transcribe_audio", fake_transcribe)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/transcribe",
        files={"file": ("sample.wav", b"\x03\x04", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "recovered"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_backend_transcribe_post_401_forced_refresh_claim_timeout_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Regression (P2 forced-refresh surfaces): when the transcription post-401
    forced refresh on the failover account hits a transient cross-replica
    refresh-CLAIM-CONTENTION timeout, the surface routes through
    ``_ensure_fresh_with_budget_or_auth_error``, which MUST surface a retryable
    ``upstream_unavailable`` (502) rather than a bogus 401 ``invalid_api_key``."""
    await _import_account(async_client, "acc_transcribe_claim_a", "transcribe-claim-a@example.com")
    await _import_account(async_client, "acc_transcribe_claim_b", "transcribe-claim-b@example.com")
    invalidated_account_id: str | None = None

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
        del audio_bytes, filename, content_type, prompt, headers, access_token, base_url, session
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        # The initial account keeps returning 401 so the surface fails over to
        # the second account and forces a refresh on it.
        raise proxy_module.ProxyResponseError(401, openai_error("invalid_api_key", "token invalidated"))

    first_fresh_account: dict[str, str | None] = {"id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_fresh_account["id"] is None:
            first_fresh_account["id"] = account.id
        if account.id != first_fresh_account["id"]:
            # The failover account's post-401 forced refresh loses to a peer
            # replica holding its refresh claim.
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module, "core_transcribe_audio", fake_transcribe)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/transcribe",
        files={"file": ("sample.wav", b"\x03\x04", "audio/wav")},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"


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

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
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
    await registry.update({"plus": [_make_upstream_model("gpt-5.1")]})

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
