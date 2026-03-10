from __future__ import annotations

import base64
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import pytest

import app.core.clients.proxy as proxy_client_module
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import UsageRepository

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


@pytest.mark.asyncio
async def test_proxy_compact_no_accounts(async_client):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "no_accounts"


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

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=25.0,
            window="primary",
            reset_at=1735689600,
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
    assert response.headers.get("x-codex-primary-reset-at") == "1735689600"
    assert response.headers.get("x-codex-credits-has-credits") == "true"
    assert response.headers.get("x-codex-credits-unlimited") == "false"
    assert response.headers.get("x-codex-credits-balance") == "12.50"


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

    monkeypatch.setattr(proxy_client_module, "get_http_client", lambda: SimpleNamespace(session=session))

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

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=15.0,
            window="secondary",
            reset_at=1735689600,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=80.0,
            window="primary",
            reset_at=1735862400,
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
    assert response.headers.get("x-codex-secondary-reset-at") == "1735862400"


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

    async def fake_ensure_fresh(self, account, force: bool = False):
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
