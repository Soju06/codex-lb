from __future__ import annotations

import base64
import contextlib
import json
from datetime import timedelta, timezone
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.core.clients.proxy as proxy_client_module
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str, *, plan_type: str = "plus") -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self.reason = "OK"
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, *, content_type=None):
        return self._payload

    def __await__(self):
        async def _return_self():
            return self

        return _return_self().__await__()


class _JsonSession:
    def __init__(self, response: _JsonResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


def _session_call_url(session: _JsonSession) -> str:
    return cast(str, session.calls[0]["url"])


def _session_call_json(session: _JsonSession) -> dict[str, object]:
    return cast(dict[str, object], session.calls[0]["json"])


async def _create_api_key(
    *,
    name: str,
    assigned_account_ids: list[str] | None = None,
) -> tuple[str, str]:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name=name,
                allowed_models=None,
                assigned_account_ids=assigned_account_ids,
            )
        )
    return created.id, created.key


@pytest.mark.asyncio
async def test_proxy_compact_no_accounts(async_client):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_proxy_compact_rejects_untrimmable_lite_prelude_before_account_selection(async_client):
    payload = {
        "model": "gpt-5.6-sol",
        "instructions": "",
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "format": {
                            "type": "grammar",
                            "syntax": "lark",
                            "definition": "x" * 500_000,
                        },
                    }
                ],
            },
            {"type": "message", "role": "developer", "content": "instructions"},
        ],
    }

    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "responses_compact_input_too_large"
    assert error["param"] == "input"


@pytest.mark.asyncio
async def test_proxy_compact_strips_tool_fields_before_upstream(async_client, monkeypatch):
    email = "compact-tools@example.com"
    raw_account_id = "acc_compact_tools"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_payloads: list[dict[str, object]] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del headers, access_token, account_id
        seen_payloads.append(cast(dict[str, object], payload.to_payload()))
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tools": [{"type": "image_generation"}],
        "tool_choice": {"type": "image_generation"},
        "parallel_tool_calls": True,
        "text": {"verbosity": "low"},
    }
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert len(seen_payloads) == 1
    assert seen_payloads[0]["model"] == "gpt-5.1"
    assert seen_payloads[0]["instructions"] == "hi"
    assert seen_payloads[0]["input"] == []
    assert "tools" not in seen_payloads[0]
    assert "tool_choice" not in seen_payloads[0]
    assert seen_payloads[0]["parallel_tool_calls"] is False
    assert "text" not in seen_payloads[0]


@pytest.mark.asyncio
async def test_proxy_compact_preserves_historical_code_mode_side_effect_pair_before_ordinary_tail(
    async_client, monkeypatch
):
    email = "compact-side-effect@example.com"
    raw_account_id = "acc_compact_side_effect"
    files = {"auth_json": ("auth.json", json.dumps(_make_auth_json(raw_account_id, email)), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_payloads: list[dict[str, object]] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del headers, access_token, account_id
        seen_payloads.append(cast(dict[str, object], payload.to_payload()))
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    side_effect_call = {
        "type": "custom_tool_call",
        "name": "exec",
        "call_id": "call-code-mode-exec",
        "input": json.dumps({"command": "git status --short"}),
    }
    side_effect_output = {
        "type": "custom_tool_call_output",
        "call_id": "call-code-mode-exec",
        "output": "clean",
    }
    ordinary_tail = {"role": "assistant", "content": "ordinary tail " + "x" * 500_000}
    payload = {
        "model": "gpt-5.6-sol",
        "instructions": "",
        "input": [
            {"role": "user", "content": "perform a code-mode action"},
            {"role": "assistant", "content": "prefix " + "y" * 260_000},
            side_effect_call,
            side_effect_output,
            ordinary_tail,
            {"role": "user", "content": "continue after compaction"},
        ],
    }

    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert len(seen_payloads) == 1
    upstream_input = seen_payloads[0]["input"]
    assert isinstance(upstream_input, list)
    assert side_effect_call in upstream_input
    assert side_effect_output in upstream_input
    assert all(
        not (
            isinstance(item, dict)
            and item.get("role") == "assistant"
            and item.get("content") == [{"type": "output_text", "text": ordinary_tail["content"]}]
        )
        for item in upstream_input
    )


@pytest.mark.asyncio
async def test_proxy_compact_surfaces_additional_quota_exhausted(async_client):
    email = "compact-gated@example.com"
    raw_account_id = "acc_compact_gated"
    auth_json = _make_auth_json(raw_account_id, email, plan_type="pro")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=25.0,
            window="primary",
            reset_at=now_epoch + 300,
            window_minutes=5,
            recorded_at=now,
        )
        await additional_repo.add_entry(
            account_id=expected_account_id,
            limit_name="codex_other",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=100.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
            recorded_at=now,
        )

    payload = {"model": "gpt-5.3-codex-spark", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "quota_exhausted"


@pytest.mark.asyncio
async def test_proxy_compact_success(async_client, monkeypatch):
    email = "compact@example.com"
    raw_account_id = "acc_compact"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    seen = {}

    async def fake_compact(payload, headers, access_token, account_id):
        seen["access_token"] = access_token
        seen["account_id"] = account_id
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    primary_reset = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 300
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=25.0,
            window="primary",
            reset_at=primary_reset,
            recorded_at=utcnow(),
            credits_has=True,
            credits_unlimited=False,
            credits_balance=12.5,
        )

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["output"] == []
    assert seen["access_token"] == "access-token"
    assert seen["account_id"] == raw_account_id
    assert response.headers.get("x-codex-primary-used-percent") == "25.0"
    assert response.headers.get("x-codex-primary-window-minutes") == "300"
    assert response.headers.get("x-codex-primary-reset-at") == str(primary_reset)
    assert response.headers.get("x-codex-credits-has-credits") == "true"
    assert response.headers.get("x-codex-credits-unlimited") == "false"
    assert response.headers.get("x-codex-credits-balance") == "12.50"


@pytest.mark.asyncio
async def test_proxy_compact_normalizes_summary_output_for_codex_remote_v2(async_client, monkeypatch):
    email = "compact-v2-summary@example.com"
    raw_account_id = "acc_compact_v2_summary"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token, account_id
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "output": [
                    {
                        "id": "msg_compact_v2",
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "historical user text"}],
                    },
                    {
                        "id": "cmp_compact_v2",
                        "type": "compaction_summary",
                        "encrypted_content": "enc_compact_v2",
                    },
                ],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {
        "model": "gpt-5.5",
        "instructions": "Compact the conversation.",
        "input": [{"type": "message", "role": "user", "content": "hello"}],
    }
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    compact_json = response.json()
    assert compact_json["object"] == "response.compaction"
    assert compact_json["output"] == [
        {
            "id": "cmp_compact_v2",
            "type": "compaction",
            "encrypted_content": "enc_compact_v2",
        }
    ]


@pytest.mark.asyncio
async def test_proxy_compact_headers_include_monthly_only_credits(async_client, monkeypatch):
    email = "compact-monthly@example.com"
    raw_account_id = "acc_compact_monthly"
    auth_json = _make_auth_json(raw_account_id, email, plan_type="free")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    monthly_reset = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 30 * 24 * 3600
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=40.0,
            window="monthly",
            reset_at=monthly_reset,
            window_minutes=43200,
            recorded_at=utcnow(),
            credits_has=True,
            credits_unlimited=False,
            credits_balance=8.75,
        )

    await get_rate_limit_headers_cache().invalidate()

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.headers.get("x-codex-monthly-used-percent") == "40.0"
    assert response.headers.get("x-codex-monthly-window-minutes") == "43200"
    assert response.headers.get("x-codex-monthly-reset-at") == str(monthly_reset)
    assert response.headers.get("x-codex-credits-has-credits") == "true"
    assert response.headers.get("x-codex-credits-unlimited") == "false"
    assert response.headers.get("x-codex-credits-balance") == "8.75"


@pytest.mark.asyncio
async def test_proxy_compact_hides_upstream_quota_for_api_key_clients_when_setting_enabled(async_client, monkeypatch):
    email = "compact-hidden@example.com"
    raw_account_id = "acc_compact_hidden"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=25.0,
            window="primary",
            reset_at=now_epoch + 300,
            window_minutes=5,
            recorded_at=now,
            credits_has=True,
            credits_unlimited=False,
            credits_balance=12.5,
        )

    _, key = await _create_api_key(name="compact-hidden", assigned_account_ids=[expected_account_id])

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    settings = await async_client.put(
        "/api/settings",
        json={
            "apiKeyAuthEnabled": True,
            "hideUpstreamQuotaFromApiKeys": True,
        },
    )
    assert settings.status_code == 200

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 200
    assert response.json()["output"] == []
    assert response.headers.get("x-codex-primary-used-percent") is None
    assert response.headers.get("x-codex-primary-window-minutes") is None
    assert response.headers.get("x-codex-primary-reset-at") is None
    assert response.headers.get("x-codex-credits-has-credits") is None
    assert response.headers.get("x-codex-credits-unlimited") is None
    assert response.headers.get("x-codex-credits-balance") is None


@pytest.mark.asyncio
async def test_proxy_compact_success_preserves_compaction_payload(async_client, monkeypatch):
    email = "compact-pass-through@example.com"
    raw_account_id = "acc_compact_pass_through"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    session = _JsonSession(
        _JsonResponse(
            {
                "object": "response.compaction",
                "compaction_summary": {
                    "encrypted_content": "enc_compact_summary_1",
                    "summary_text": "condensed thread state",
                },
            }
        )
    )

    @contextlib.asynccontextmanager
    async def lease_session(session_override=None):
        assert session_override is None
        yield session

    monkeypatch.setattr(proxy_client_module, "lease_http_session", lease_session)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response.compaction"
    assert body["compaction_summary"] == {
        "encrypted_content": "enc_compact_summary_1",
        "summary_text": "condensed thread state",
    }
    assert _session_call_url(session).endswith("/codex/responses/compact")
    call_json = _session_call_json(session)
    assert "stream" not in call_json
    assert "store" not in call_json


@pytest.mark.asyncio
async def test_proxy_compact_masks_previous_response_not_found(async_client, monkeypatch):
    email = "compact-prev-missing@example.com"
    raw_account_id = "acc_compact_prev_missing"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token, account_id
        error_payload = openai_error(
            "invalid_request_error",
            "Previous response with id 'resp_compact_missing' not found.",
            error_type="invalid_request_error",
        )
        error_payload["error"]["param"] = "previous_response_id"
        raise ProxyResponseError(400, error_payload)

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "stream_incomplete"
    assert body["error"]["message"] == "Upstream websocket closed before response.completed"
    assert "resp_compact_missing" not in response.text


@pytest.mark.asyncio
async def test_proxy_compact_headers_normalize_weekly_only_with_stale_secondary(async_client, monkeypatch):
    email = "compact-weekly@example.com"
    raw_account_id = "acc_compact_weekly"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    now = utcnow()

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    weekly_reset = now_epoch + 6 * 24 * 3600
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=15.0,
            window="secondary",
            reset_at=now_epoch + 5 * 24 * 3600,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=80.0,
            window="primary",
            reset_at=weekly_reset,
            window_minutes=10080,
            recorded_at=now,
        )

    await get_rate_limit_headers_cache().invalidate()

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.headers.get("x-codex-primary-used-percent") is None
    assert response.headers.get("x-codex-secondary-used-percent") == "80.0"
    assert response.headers.get("x-codex-secondary-window-minutes") == "10080"
    assert response.headers.get("x-codex-secondary-reset-at") == str(weekly_reset)


@pytest.mark.asyncio
async def test_proxy_compact_headers_expire_elapsed_primary_rows(async_client, monkeypatch):
    email = "compact-expired@example.com"
    raw_account_id = "acc_compact_expired"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    weekly_reset = now_epoch + 5 * 24 * 3600

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        # Upstream stopped reporting the primary window: the frozen 87%
        # sample with an elapsed reset must not be served downstream.
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=87.0,
            window="primary",
            reset_at=now_epoch - 7200,
            window_minutes=300,
            recorded_at=now - timedelta(hours=3),
        )
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=40.0,
            window="secondary",
            reset_at=weekly_reset,
            window_minutes=10080,
            recorded_at=now,
        )

    await get_rate_limit_headers_cache().invalidate()

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.headers.get("x-codex-primary-used-percent") == "0.0"
    assert response.headers.get("x-codex-primary-reset-at") is None
    assert response.headers.get("x-codex-secondary-used-percent") == "40.0"
    assert response.headers.get("x-codex-secondary-reset-at") == str(weekly_reset)


@pytest.mark.asyncio
async def test_proxy_compact_usage_limit_marks_account(async_client, monkeypatch):
    email = "limit@example.com"
    raw_account_id = "acc_limit"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)

    async def fake_compact(payload, headers, access_token, account_id):
        raise ProxyResponseError(
            429,
            {
                "error": {
                    "type": "usage_limit_reached",
                    "message": "limit reached",
                    "plan_type": "plus",
                    "resets_at": 1767612327,
                }
            },
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "usage_limit_reached"

    async with SessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        assert account.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_proxy_compact_retry_uses_refreshed_account_id(async_client, monkeypatch):
    email = "compact-retry@example.com"
    raw_account_id = "acc_compact_retry_old"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    captured_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        captured_account_ids.append(account_id)
        if len(captured_account_ids) == 1:
            raise ProxyResponseError(
                401,
                openai_error("invalid_api_key", "token expired"),
            )
        return OpenAIResponsePayload.model_validate({"output": []})

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        if force:
            account.chatgpt_account_id = "acc_compact_retry_new"
        return account

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["output"] == []
    assert captured_account_ids == ["acc_compact_retry_old", "acc_compact_retry_new"]


@pytest.mark.asyncio
async def test_proxy_compact_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    first_email = "compact-invalidated-a@example.com"
    first_raw_account_id = "acc_compact_invalidated_a"
    first_auth_json = _make_auth_json(first_raw_account_id, first_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-a.json", json.dumps(first_auth_json), "application/json")},
    )
    assert response.status_code == 200

    second_email = "compact-invalidated-b@example.com"
    second_raw_account_id = "acc_compact_invalidated_b"
    second_auth_json = _make_auth_json(second_raw_account_id, second_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-b.json", json.dumps(second_auth_json), "application/json")},
    )
    assert response.status_code == 200

    first_account_id = generate_unique_account_id(first_raw_account_id, first_email)
    second_account_id = generate_unique_account_id(second_raw_account_id, second_email)
    first_upstream_account_id = "chatgpt_compact_invalidated_a"
    second_upstream_account_id = "chatgpt_compact_invalidated_b"

    async with SessionLocal() as session:
        first_account = await session.get(Account, first_account_id)
        assert first_account is not None
        first_account.chatgpt_account_id = first_upstream_account_id
        second_account = await session.get(Account, second_account_id)
        assert second_account is not None
        second_account.chatgpt_account_id = second_upstream_account_id
        await session.commit()

    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise ProxyResponseError(
                401,
                openai_error(
                    "invalid_api_key",
                    "Your authentication token has been invalidated. Please try signing in again.",
                    error_type="authentication_error",
                ),
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        return account

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] in {first_upstream_account_id, second_upstream_account_id}
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_proxy_compact_token_invalidated_marks_reauth_and_fails_over(async_client, monkeypatch):
    first_email = "compact-token-invalidated-a@example.com"
    first_raw_account_id = "acc_compact_token_invalidated_a"
    first_auth_json = _make_auth_json(first_raw_account_id, first_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-a.json", json.dumps(first_auth_json), "application/json")},
    )
    assert response.status_code == 200

    second_email = "compact-token-invalidated-b@example.com"
    second_raw_account_id = "acc_compact_token_invalidated_b"
    second_auth_json = _make_auth_json(second_raw_account_id, second_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-b.json", json.dumps(second_auth_json), "application/json")},
    )
    assert response.status_code == 200

    first_account_id = generate_unique_account_id(first_raw_account_id, first_email)
    second_account_id = generate_unique_account_id(second_raw_account_id, second_email)
    first_upstream_account_id = "chatgpt_compact_token_invalidated_a"
    second_upstream_account_id = "chatgpt_compact_token_invalidated_b"

    async with SessionLocal() as session:
        first_account = await session.get(Account, first_account_id)
        assert first_account is not None
        first_account.chatgpt_account_id = first_upstream_account_id
        second_account = await session.get(Account, second_account_id)
        assert second_account is not None
        second_account.chatgpt_account_id = second_upstream_account_id
        await session.commit()

    captured_account_ids: list[str | None] = []
    invalidated_upstream_account_id: str | None = None

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal invalidated_upstream_account_id
        if invalidated_upstream_account_id is None:
            invalidated_upstream_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_upstream_account_id:
            raise ProxyResponseError(
                401,
                openai_error(
                    "token_invalidated",
                    "Your authentication token has been invalidated. Please try signing in again.",
                    error_type="authentication_error",
                ),
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        return account

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert captured_account_ids[:2] == [invalidated_upstream_account_id, invalidated_upstream_account_id]
    assert captured_account_ids[2] in {first_upstream_account_id, second_upstream_account_id}
    assert captured_account_ids[2] != invalidated_upstream_account_id

    invalidated_account_id = (
        first_account_id if invalidated_upstream_account_id == first_upstream_account_id else second_account_id
    )

    async with SessionLocal() as session:
        invalidated_account = await session.get(Account, invalidated_account_id)
        assert invalidated_account is not None
        assert invalidated_account.status == AccountStatus.REAUTH_REQUIRED
        assert invalidated_account.deactivation_reason is not None
        assert "re-login required" in invalidated_account.deactivation_reason

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = {account["accountId"]: account for account in accounts_response.json()["accounts"]}
    assert accounts[invalidated_account_id]["status"] == "reauth_required"

    overview_response = await async_client.get("/api/dashboard/overview")
    assert overview_response.status_code == 200
    overview_accounts = {account["accountId"]: account for account in overview_response.json()["accounts"]}
    assert overview_accounts[invalidated_account_id]["status"] == "reauth_required"


@pytest.mark.asyncio
async def test_proxy_compact_repeated_401_settles_reservation_if_error_recording_fails(async_client, monkeypatch):
    email = "compact-invalidated-settle@example.com"
    raw_account_id = "acc_compact_invalidated_settle"
    auth_json = _make_auth_json(raw_account_id, email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert response.status_code == 200

    compact_calls = 0

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal compact_calls
        compact_calls += 1
        raise ProxyResponseError(
            401,
            openai_error(
                "invalid_api_key",
                "Your authentication token has been invalidated. Please try signing in again.",
                error_type="authentication_error",
            ),
        )

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        return account

    async def fake_handle_proxy_error(self, account, exc):
        raise RuntimeError("account health store unavailable")

    settle_compact_usage = AsyncMock()

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_proxy_error", fake_handle_proxy_error)
    monkeypatch.setattr(proxy_module.ProxyService, "_settle_compact_api_key_usage", settle_compact_usage)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    with pytest.raises(RuntimeError, match="account health store unavailable"):
        await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert compact_calls == 2
    settle_compact_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_proxy_compact_pinned_preflight_claim_timeout_settles_reservation(async_client, monkeypatch):
    """Regression: a file/previous-response-pinned compact whose freshness-check
    preflight hits a transient refresh-claim timeout MUST settle the API-key
    reservation before surfacing the retryable ``upstream_unavailable``.

    On the HTTP bridge / forwarded path ``_stream_responses`` passes an
    ``api_key_reservation_override`` with ``owns_reservation`` false, making
    ``compact_responses`` responsible for settling the reservation. A pinned
    request cannot fail over, so the pinned preflight branch surfaces the
    retryable ``upstream_unavailable`` instead of continuing. Before the fix that
    branch raised via ``_raise_proxy_unavailable`` BEFORE calling
    ``_settle_compact_api_key_usage`` (unlike the sibling post-401 forced-refresh
    pinned branch), leaving the reservation unfinished and holding API-key quota.
    """
    from app.core.auth.refresh import RefreshError

    email = "compact-pinned-preflight-settle@example.com"
    raw_account_id = "acc_compact_pinned_preflight_settle"
    auth_json = _make_auth_json(raw_account_id, email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        owner_account_id = (await session.execute(select(Account.id))).scalars().one()

    # Pin the turn to the owner account so ``preferred_account_id`` is set and the
    # request cannot cross accounts on the transient claim timeout.
    async def fake_owner(self, *, previous_response_id, api_key, session_id=None, surface):
        del self, previous_response_id, api_key, session_id, surface
        return owner_account_id

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_owner)

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        del self, account, force, timeout_seconds
        raise RefreshError(
            "refresh_claim_timeout",
            "refresh claim held by another replica",
            False,
            transport_error=True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    settle_compact_usage = AsyncMock()
    monkeypatch.setattr(proxy_module.ProxyService, "_settle_compact_api_key_usage", settle_compact_usage)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "previous_response_id": "resp_pinned_owner",
    }
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    # Pinned transient contention surfaces as retryable upstream_unavailable (502).
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    # The reservation was settled before the branch raised (the fix): had the
    # branch raised first, the reservation would leak API-key quota.
    settle_compact_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_proxy_compact_pinned_preflight_transport_error_settles_reservation(async_client, monkeypatch):
    """Regression (finding #5): a file/previous-response-pinned compact whose
    freshness-check preflight fails with a GENUINE OAuth ``transport_error``
    (NOT claim contention) MUST settle the API-key reservation before raising the
    retryable ``upstream_unavailable``. On the HTTP bridge / forwarded path
    (``owns_reservation`` false) ``compact_responses`` is the sole settler; the
    pinned transport-error preflight branch previously raised via
    ``_raise_proxy_unavailable`` WITHOUT settling, leaking API-key quota (the
    claim-contention sibling settled, but the transport-error/permanent siblings
    did not)."""
    from app.core.auth.refresh import RefreshError

    email = "compact-pinned-preflight-transport@example.com"
    raw_account_id = "acc_compact_pinned_preflight_transport"
    auth_json = _make_auth_json(raw_account_id, email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        owner_account_id = (await session.execute(select(Account.id))).scalars().one()

    async def fake_owner(self, *, previous_response_id, api_key, session_id=None, surface):
        del self, previous_response_id, api_key, session_id, surface
        return owner_account_id

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_owner)

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        del self, account, force, timeout_seconds
        raise RefreshError(
            "transport_error",
            "oauth refresh upstream timed out",
            False,
            transport_error=True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    settle_compact_usage = AsyncMock()
    monkeypatch.setattr(proxy_module.ProxyService, "_settle_compact_api_key_usage", settle_compact_usage)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "previous_response_id": "resp_pinned_owner_transport",
    }
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    # The reservation is settled before the pinned transport-error branch raises.
    settle_compact_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_proxy_compact_preflight_permanent_refresh_settles_reservation(async_client, monkeypatch):
    """Regression (finding #5): a permanent ``RefreshError`` on the compact
    freshness-check preflight MUST settle the API-key reservation before
    propagating (bridge/forwarded path: ``owns_reservation`` false, so
    ``compact_responses`` is the sole settler). The permanent preflight branch
    previously re-raised WITHOUT settling, leaking API-key quota."""
    from app.core.auth.refresh import RefreshError

    email = "compact-preflight-permanent-settle@example.com"
    raw_account_id = "acc_compact_preflight_permanent_settle"
    auth_json = _make_auth_json(raw_account_id, email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert response.status_code == 200

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        del self, account, force, timeout_seconds
        raise RefreshError(
            "invalid_grant",
            "refresh token permanently rejected",
            True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    settle_compact_usage = AsyncMock()
    monkeypatch.setattr(proxy_module.ProxyService, "_settle_compact_api_key_usage", settle_compact_usage)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    # The permanent preflight failure keeps its prior escalation (it propagates
    # to the caller). Crucially the reservation is settled BEFORE that raise (the
    # fix): pre-fix the permanent preflight branch re-raised without settling,
    # leaking API-key quota on the bridge/forwarded path.
    with pytest.raises(RefreshError):
        await async_client.post("/backend-api/codex/responses/compact", json=payload)

    settle_compact_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_proxy_compact_forwarded_bridge_preflight_budget_exhausted_settles_reservation(async_client, monkeypatch):
    """Regression (route-level, forwarded bridge path): a compact request that
    reaches the OWNER instance via the internal bridge forward — where
    ``owns_reservation`` is false so ``compact_responses`` is the SOLE settler —
    and whose preflight budget is exhausted MUST settle (release) the API-key
    usage reservation before raising the ``502 upstream_request_timeout``, so
    held API-key quota is not leaked.

    This drives the REAL external surface, not a handcrafted service call: it
    POSTs a signed forwarded request to the internal bridge endpoint
    (``/internal/bridge/responses``) carrying a real ``ApiKeyUsageReservation``
    (the reservation the ORIGIN instance created via ``_enforce_request_limits``,
    reproduced here through the api-keys service). ``internal_bridge_responses``
    parses the forward, sets ``skip_limit_enforcement`` + the
    ``api_key_reservation_override``, and ``_stream_responses`` extracts the
    terminal ``compaction_trigger`` and calls ``compact_responses`` with
    ``owns_reservation`` false — so ``_compact_or_stream_responses``'s ``finally``
    does NOT release the reservation and ``compact_responses`` alone must settle
    it. Pre-fix the budget-exhausted terminal raised via
    ``_raise_proxy_budget_exhausted`` without settling (through the outer
    ``except ProxyResponseError`` handler and the log-only ``finally``), leaving
    the reservation row ``reserved`` (leaked held quota); post-fix the row is
    ``released``. PR #1254 fixed the sibling transport-failure / permanent-refresh
    preflight raises but left the budget-exhausted terminal out of scope; this
    completes that invariant.
    """
    import app.modules.proxy._service.compact as compact_module
    from app.core.config.settings import get_settings
    from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
    from app.db.models import ApiKeyUsageReservation
    from app.modules.api_keys.service import ApiKeysService, ApiKeyUsageReservationData
    from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
    from app.modules.proxy.http_bridge_forwarding import HTTPBridgeForwardContext, build_owner_forward_headers

    # Import an account so the compact selection loop has a healthy candidate.
    email = "compact-forwarded-budget@example.com"
    raw_account_id = "acc_compact_forwarded_budget"
    auth_json = _make_auth_json(raw_account_id, email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert response.status_code == 200

    # Enable API-key auth so the owner instance validates the forwarded key and
    # compact_responses receives a non-None api_key (otherwise the settle no-ops
    # and there is no reservation to leak).
    settings_resp = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings_resp.status_code == 200

    create = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "compact-forwarded-budget-key",
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000_000}],
        },
    )
    assert create.status_code == 200
    key_id = create.json()["id"]
    key = create.json()["key"]

    # Reproduce the origin instance's reservation: _enforce_request_limits creates
    # a real "reserved" ApiKeyUsageReservation row that the forward carries by id.
    compact_model = ResponsesCompactRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})
    async with SessionLocal() as session:
        api_keys_service = ApiKeysService(ApiKeysRepository(session))
        reservation = await api_keys_service.enforce_limits_for_request(
            key_id,
            request_model=compact_model.model,
            request_service_tier=None,
            request_usage_budget=estimate_api_key_request_usage(compact_model),
        )
    async with SessionLocal() as session:
        row = await session.get(ApiKeyUsageReservation, reservation.reservation_id)
        assert row is not None
        assert row.status == "reserved"

    # Build the signed forward the origin would send to this (owner) instance: a
    # ResponsesRequest whose input ends with a compaction_trigger (so the owner
    # extracts the compact payload), targeting this instance, carrying the
    # reservation override (owns_reservation=false on the owner).
    forwarded_payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hello"}, {"type": "compaction_trigger"}],
            "stream": True,
        }
    )
    context = HTTPBridgeForwardContext(
        origin_instance="origin-instance",
        target_instance=get_settings().http_responses_session_bridge_instance_id,
        codex_session_affinity=True,
        downstream_turn_state=None,
        reservation=ApiKeyUsageReservationData(
            reservation_id=reservation.reservation_id,
            key_id=key_id,
            model=compact_model.model,
        ),
    )
    headers = build_owner_forward_headers(
        headers={"authorization": f"Bearer {key}"},
        payload=forwarded_payload,
        context=context,
    )

    # Force the compact preflight budget to read as exhausted (account selection
    # uses the real service.py deadline, so a healthy account is still selected;
    # the first compact-module budget check then trips the budget-exhausted
    # terminal before any upstream/freshness work runs).
    monkeypatch.setattr(compact_module, "_remaining_budget_seconds", lambda deadline: 0.0)

    response = await async_client.post(
        "/internal/bridge/responses",
        json=forwarded_payload.model_dump_for_forwarding(),
        headers=headers,
    )

    # Budget exhaustion surfaces as a 502 upstream_request_timeout from the owner.
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "upstream_request_timeout"

    # The forwarded reservation row was RELEASED by compact_responses (sole
    # settler) before the terminal raised (the fix). Pre-fix it stayed "reserved"
    # — leaked held API-key quota — because owns_reservation is false on the
    # forwarded path so the route's finally does not release it.
    async with SessionLocal() as session:
        row = await session.get(ApiKeyUsageReservation, reservation.reservation_id)
        assert row is not None
        assert row.status == "released", f"forwarded reservation leaked held quota; status={row.status!r}"


@pytest.mark.asyncio
async def test_proxy_compact_retryable_transport_failure_retries_same_contract_only(async_client, monkeypatch):
    email = "compact-safe-retry@example.com"
    raw_account_id = "acc_compact_safe_retry"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    compact_calls: list[str | None] = []
    stream_calls: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_calls.append(account_id)
        if len(compact_calls) == 1:
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "temporary compact failure"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "output": [{"type": "reasoning", "encrypted_content": "enc_retry_success"}],
            }
        )

    async def fake_stream(*args, **kwargs):
        stream_calls.append("called")
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert compact_calls == [raw_account_id, raw_account_id]
    assert stream_calls == []


@pytest.mark.asyncio
async def test_proxy_compact_ambiguous_process_network_failure_is_neutral_and_not_replayed(
    async_client,
    monkeypatch,
):
    email = "compact-network-neutral@example.com"
    raw_account_id = "acc_compact_network_neutral"
    auth_json = _make_auth_json(raw_account_id, email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert response.status_code == 200
    compact_calls = 0

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal compact_calls
        del payload, headers, access_token, account_id
        compact_calls += 1
        raise ProxyResponseError(
            502,
            openai_error("proxy_network_unavailable", "Temporary local network failure"),
            failure_phase="request",
            retryable_same_contract=False,
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "hi", "input": []},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "proxy_network_unavailable"
    assert compact_calls == 1
    async with SessionLocal() as session:
        account = await session.get(Account, generate_unique_account_id(raw_account_id, email))
        assert account is not None
        assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_proxy_compact_output_round_trips_into_followup_responses_without_pruning(async_client, monkeypatch):
    email = "compact-round-trip@example.com"
    raw_account_id = "acc_compact_round_trip"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    compact_window = {
        "object": "response.compaction",
        "output": [
            {
                "type": "message",
                "id": "msg_compact_round_trip",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "preserve me exactly"}],
            },
            {"type": "reasoning", "encrypted_content": "enc_round_trip_state"},
        ],
        "retained_items": [{"type": "item_reference", "id": "msg_original_round_trip"}],
    }
    seen_inputs: list[object] = []

    async def fake_compact(payload, headers, access_token, account_id):
        return CompactResponsePayload.model_validate(compact_window)

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_inputs.append(payload.input)
        yield 'data: {"type":"response.completed","response":{"id":"resp_round_trip"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    compact_payload = {"model": "gpt-5.1", "instructions": "compact", "input": []}
    compact_response = await async_client.post("/backend-api/codex/responses/compact", json=compact_payload)
    assert compact_response.status_code == 200
    assert compact_response.json() == compact_window

    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "continue",
        "input": compact_response.json()["output"],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload)

    assert response.status_code == 200
    assert seen_inputs == [compact_window["output"]]
