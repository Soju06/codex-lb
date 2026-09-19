from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, RequestLog, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.dependencies import _proxy_repo_context
from app.modules.proxy.load_balancer import AccountSelection

pytestmark = pytest.mark.integration


ANTHROPIC_SSE_BYTES = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123","type":"message",'
    b'"role":"assistant","model":"claude-sonnet-4-20250514","content":[],"usage":{"input_tokens":10,'
    b'"cache_creation_input_tokens":3,"cache_read_input_tokens":4}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":5}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


@pytest.mark.asyncio
async def test_anthropic_messages_returns_error_when_no_accounts_available(async_client):
    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "no_available_anthropic_accounts",
            "message": (
                "No available accounts. Service is operating in degraded mode: all upstream accounts are unavailable"
            ),
        },
    }


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.content = _FakeContent([body])
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body


class _FakeResponseContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def test_anthropic_rate_limit_error_parses_unified_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"rate limited"}}',
        headers={
            "anthropic-ratelimit-unified-reset": "2026-06-09T19:50:00.000000+00:00",
            "retry-after": "37",
        },
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "rate limited")

    assert error["resets_at"] == 1781034600
    assert error["resets_in_seconds"] == 37


def test_anthropic_rate_limit_error_parses_epoch_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"rate limited"}}',
        headers={"anthropic-ratelimit-unified-reset": "1781034600"},
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "rate limited")

    assert error["resets_at"] == 1781034600


def test_anthropic_rate_limit_error_parses_fast_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"fast pool exhausted"}}',
        headers={"anthropic-fast-input-tokens-reset": "1781034600"},
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "fast pool exhausted")

    assert error["resets_at"] == 1781034600


def test_anthropic_sticky_key_uses_quota_scoped_session_hash():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        }
    )

    key = anthropic_proxy_module._anthropic_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        quota_key=anthropic_proxy_module._anthropic_quota_key(payload),
    )

    assert key is not None
    assert key.startswith("claude:anthropic_top_thinking:session:")
    assert "session-123" not in key


def test_anthropic_fast_mode_uses_fast_quota_and_model_affinity():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "speed": "fast",
            "thinking": {"type": "adaptive"},
        }
    )

    quota_key = anthropic_proxy_module._messages_quota_key(payload, provider_name="anthropic")
    affinity_quota_key = anthropic_proxy_module._messages_affinity_quota_key(payload, provider_name="anthropic")
    key = anthropic_proxy_module._messages_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        provider_name="anthropic",
        quota_key=affinity_quota_key,
    )

    assert quota_key == "anthropic_fast"
    assert affinity_quota_key == "anthropic_top_thinking_fable"
    assert key is not None
    assert key.startswith("claude:anthropic_top_thinking_fable:session:")


def test_anthropic_fast_beta_header_without_fast_speed_uses_model_quota():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "speed": "standard",
            "thinking": {"type": "adaptive"},
        }
    )

    quota_key = anthropic_proxy_module._messages_quota_key(payload, provider_name="anthropic")
    key = anthropic_proxy_module._messages_sticky_key(
        payload,
        {"anthropic-beta": "fast-mode-2026-02-01", "x-claude-session-id": "session-123"},
        provider_name="anthropic",
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(payload, provider_name="anthropic"),
    )

    assert quota_key == "anthropic_top_thinking"
    assert key is not None
    assert key.startswith("claude:anthropic_top_thinking_fable:session:")


def test_messages_affinity_quota_key_scopes_fable_class_separately():
    fable_payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        }
    )
    opus_payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-opus-4-8",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        }
    )
    haiku_payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-haiku-4-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    # Fable-class traffic gets a distinct affinity family; non-Fable models
    # (opus, haiku) keep the unchanged, unsuffixed quota key.
    assert (
        anthropic_proxy_module._messages_affinity_quota_key(fable_payload, provider_name="anthropic")
        == "anthropic_top_thinking_fable"
    )
    assert (
        anthropic_proxy_module._messages_affinity_quota_key(opus_payload, provider_name="anthropic")
        == "anthropic_top_thinking"
    )
    assert (
        anthropic_proxy_module._messages_affinity_quota_key(haiku_payload, provider_name="anthropic")
        == "anthropic_standard"
    )


def test_glm_messages_derive_glm_provider_quota_and_sticky_key():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "glm-5.2",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "enabled"},
        }
    )

    assert anthropic_proxy_module._messages_provider_name(payload) == "glm"
    quota_key = anthropic_proxy_module._messages_quota_key(payload, provider_name="glm")
    key = anthropic_proxy_module._messages_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        provider_name="glm",
        quota_key=quota_key,
    )

    assert quota_key == "glm_coding_thinking"
    assert key is not None
    assert key.startswith("glm:glm_coding_thinking:session:")
    assert "session-123" not in key


async def _insert_account(
    *,
    account_id: str,
    provider: str,
    access_token: str,
    email: str,
    status: AccountStatus = AccountStatus.ACTIVE,
    subscription_status: str | None = None,
) -> None:
    encryptor = TokenEncryptor()
    plan_type = "max" if provider == "anthropic" else "glm-coding" if provider == "glm" else "plus"
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider=provider,
                chatgpt_account_id=f"workspace-{account_id}" if provider == "openai" else account_id,
                email=email,
                plan_type=plan_type,
                access_token_encrypted=encryptor.encrypt(access_token),
                refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                id_token_encrypted=encryptor.encrypt(f"id-{account_id}") if provider == "openai" else None,
                last_refresh=utcnow() + timedelta(days=1),
                status=status,
                deactivation_reason=None,
                subscription_status=subscription_status,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_anthropic_session_route_surfaces_provider_status_failure(async_client):
    for index in range(3):
        await _insert_account(
            account_id=f"anthropic-rate-limited-{index}",
            provider="anthropic",
            access_token=f"anthropic-access-{index}",
            email=f"claude-{index}@example.com",
            status=AccountStatus.RATE_LIMITED,
        )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-rate-limited-accounts",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "message": (
                "3 Anthropic accounts exist, but none are selectable for "
                "claude-fable-5/anthropic_top_thinking; statuses: rate_limited=3. "
                "OpenAI accounts are not eligible for Claude routing."
            ),
            "type": "server_error",
            "code": "no_available_anthropic_accounts",
        }
    }


def _disable_pool_exhausted_wait(monkeypatch) -> None:
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", "false")
    get_settings.cache_clear()


async def _insert_quota_cooldown(
    *,
    account_id: str,
    quota_key: str,
    reset_at: int,
    window: str = "primary",
    recorded_at: datetime | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            AdditionalUsageHistory(
                account_id=account_id,
                quota_key=quota_key,
                limit_name=quota_key,
                metered_feature="anthropic_messages",
                window=window,
                used_percent=100.0,
                reset_at=reset_at,
                window_minutes=10,
                recorded_at=recorded_at or utcnow(),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_anthropic_messages_quota_cooldown_returns_native_rate_limit(async_client):
    await _insert_account(
        account_id="anthropic-cooling",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert "cooling down" in body["error"]["message"]
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(reset_at)
    assert 0 < int(response.headers["retry-after"]) <= 7 * 60


@pytest.mark.asyncio
async def test_anthropic_session_route_includes_retry_metadata(async_client):
    await _insert_account(
        account_id="anthropic-cooling",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-cooldown",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "anthropic_quota_cooldown"
    assert error["retryAt"] == datetime.fromtimestamp(reset_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert 0 < error["retryAfterSeconds"] <= 7 * 60
    assert response.headers["retry-after"] == str(error["retryAfterSeconds"])


@pytest.mark.asyncio
async def test_anthropic_session_route_accepts_fast_quota_with_model_affinity(async_client):
    await _insert_account(
        account_id="anthropic-fast-preflight",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-fast",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_fast",
            "affinityQuotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["quotaKey"] == "anthropic_fast"
    assert response.json()["affinityQuotaKey"] == "anthropic_top_thinking"

    sticky_key = "claude:anthropic_top_thinking:session:" + anthropic_proxy_module._hash_for_key("session-route-fast")
    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert sticky.account_id == "anthropic-fast-preflight"


@pytest.mark.asyncio
async def test_anthropic_session_route_accepts_explicit_fable_affinity_quota_key(async_client):
    await _insert_account(
        account_id="anthropic-fable-affinity-preflight",
        provider="anthropic",
        access_token="anthropic-access-fable-affinity",
        email="fable-affinity@example.com",
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-fable-affinity",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
            "affinityQuotaKey": "anthropic_top_thinking_fable",
        },
    )

    assert response.status_code == 200
    assert response.json()["quotaKey"] == "anthropic_top_thinking"
    assert response.json()["affinityQuotaKey"] == "anthropic_top_thinking_fable"


@pytest.mark.asyncio
async def test_anthropic_session_route_explains_active_account_blocked_by_model_quota(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-active-cooling",
        provider="anthropic",
        access_token="anthropic-access-active",
        email="active-claude@example.com",
    )
    await _insert_account(
        account_id="anthropic-quota-exceeded",
        provider="anthropic",
        access_token="anthropic-access-exhausted",
        email="exhausted-claude@example.com",
        status=AccountStatus.QUOTA_EXCEEDED,
    )
    reset_at = int((utcnow() + timedelta(minutes=4)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-active-cooling",
        quota_key="anthropic_top",
        reset_at=reset_at,
    )

    async def fake_select_account(self, **kwargs):
        del self
        assert kwargs["account_ids"] == ["anthropic-quota-exceeded"]
        return AccountSelection(
            account=None,
            error_message="No available accounts",
            error_code="no_available_anthropic_accounts",
        )

    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-active-cooling",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "no_available_anthropic_accounts"
    assert "statuses: active=1, quota_exceeded=1" in error["message"]
    assert "Model quota: anthropic_top cooldown excluded 1 account" in error["message"]
    assert "1 account remained after the anthropic_top prefilter" in error["message"]
    assert error["retryAt"] == datetime.fromtimestamp(reset_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert 0 < error["retryAfterSeconds"] <= 4 * 60


@pytest.mark.asyncio
async def test_anthropic_messages_all_routable_accounts_cooling_returns_429_despite_unusable_rows(async_client):
    """Regression for the 6-account routing incident.

    When every *routable* account is cooling down on the model quota, the
    proxy must return a clean 429 with retry metadata — not a confusing
    503 that treats canceled/deactivated rows as 'remaining candidates'.
    Here one usable account is thinking-cooled while a canceled-subscription
    row and a deactivated row are present but unroutable.
    """
    await _insert_account(
        account_id="anthropic-usable-cooling",
        provider="anthropic",
        access_token="anthropic-access-usable",
        email="usable@example.com",
    )
    await _insert_account(
        account_id="anthropic-canceled-sub",
        provider="anthropic",
        access_token="anthropic-access-canceled",
        email="canceled@example.com",
        subscription_status="canceled",
    )
    await _insert_account(
        account_id="anthropic-deactivated",
        provider="anthropic",
        access_token="anthropic-access-deactivated",
        email="deactivated@example.com",
        status=AccountStatus.DEACTIVATED,
    )
    reset_at = int((utcnow() + timedelta(minutes=6)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-usable-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert "cooling down" in body["error"]["message"]
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(reset_at)
    assert 0 < int(response.headers["retry-after"]) <= 6 * 60


@pytest.mark.asyncio
async def test_anthropic_session_route_failure_counts_only_routable_accounts(async_client, monkeypatch):
    """The selection-failure message must count only routable accounts and
    label stored-but-unusable rows separately, instead of inflating the
    headline total with canceled/deactivated rows."""
    await _insert_account(
        account_id="anthropic-routable",
        provider="anthropic",
        access_token="anthropic-access-routable",
        email="routable@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    await _insert_account(
        account_id="anthropic-canceled",
        provider="anthropic",
        access_token="anthropic-access-canceled",
        email="canceled@example.com",
        subscription_status="canceled",
    )
    await _insert_account(
        account_id="anthropic-deactivated",
        provider="anthropic",
        access_token="anthropic-access-deactivated",
        email="deactivated@example.com",
        status=AccountStatus.DEACTIVATED,
    )

    async def fake_select_account(self, **kwargs):
        del self
        # Only the routable row reaches the load balancer; canceled and
        # deactivated rows are filtered out by the eligibility prefilter.
        assert kwargs["account_ids"] == ["anthropic-routable"]
        return AccountSelection(
            account=None,
            error_message="No available accounts",
            error_code="no_available_anthropic_accounts",
        )

    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-routable-count",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 503
    message = response.json()["error"]["message"]
    assert "1 Anthropic account exist" in message
    assert "statuses: rate_limited=1." in message
    assert "deactivated=1" not in message
    assert "+2 stored but not routable" in message


@pytest.mark.asyncio
async def test_anthropic_messages_mid_stream_failure_emits_sse_error_event(async_client, monkeypatch):
    # With the pool-exhausted wait disabled, a mid-stream pool-wide failure
    # must keep surfacing the immediate structured error instead of holding.
    _disable_pool_exhausted_wait(monkeypatch)
    await _insert_account(
        account_id="anthropic-only",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=9)).replace(tzinfo=timezone.utc).timestamp())

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(
                429,
                b'{"error":{"message":"top model cooldown"}}',
                headers={"anthropic-ratelimit-unified-reset": str(reset_at)},
            )
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    text = body.decode("utf-8")
    assert "event: error\n" in text
    error_data = json.loads(text.split("event: error\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert error_data["type"] == "error"
    assert error_data["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_anthropic_messages_streams_sse_and_logs_usage(async_client, monkeypatch):
    await _insert_account(
        account_id="openai-account",
        provider="openai",
        access_token="openai-access",
        email="openai@example.com",
    )
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session
        assert provider_name == "anthropic"
        captured["headers"] = dict(headers)
        captured["json_body"] = dict(json_body)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 32,
        "stream": True,
        "system": "Claude Code system prompt prefix must pass through untouched.",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "Runtime system context injected by Claude Code."},
        ],
        "thinking": {"type": "adaptive"},
        "metadata": {
            "user_id": (
                '{"device_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",'
                '"account_uuid":"","session_id":"a38d23ac-2d2f-4354-8861-5b686809b2b5"}'
            )
        },
        "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
        "output_config": {"container": {"type": "auto"}},
    }
    async with async_client.stream(
        "POST",
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "authorization": "Bearer client-token",
            "x-api-key": "client-placeholder",
            "user-agent": "claude-cli/2.1.210 (external, sdk-cli)",
            "x-claude-code-session-id": "header-session",
        },
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert body == ANTHROPIC_SSE_BYTES
    assert captured["headers"]["Authorization"] == "Bearer anthropic-access"
    assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in {key.lower() for key in captured["headers"]}
    assert captured["json_body"]["system"] == payload["system"]
    assert captured["json_body"]["messages"] == payload["messages"]
    assert captured["json_body"]["thinking"] == payload["thinking"]
    assert captured["json_body"]["context_management"] == payload["context_management"]
    assert captured["json_body"]["output_config"] == payload["output_config"]

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())

    assert len(logs) == 1
    log = logs[0]
    assert log.provider == "anthropic"
    assert log.account_id == "anthropic-account"
    assert log.status == "success"
    assert log.input_tokens == 10
    assert log.output_tokens == 5
    assert log.cache_creation_tokens == 3
    assert log.cache_read_tokens == 4
    assert log.session_id == "a38d23ac-2d2f-4354-8861-5b686809b2b5"
    assert log.useragent == "claude-cli/2.1.210 (external, sdk-cli)"
    assert log.useragent_group == "claude-cli"
    assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_anthropic_upstream_stream_error_event_is_logged_as_error(async_client, monkeypatch):
    # Anthropic reports mid-stream failures as an in-band SSE `error` event on
    # an HTTP 200 stream. The proxy must forward the bytes verbatim (the
    # request is not retryable once output started) but the request log must
    # record the failure instead of a success (2026-08-18 audit).
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    upstream_bytes = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123","type":"message",'
        b'"role":"assistant","model":"claude-fable-5","content":[],"usage":{"input_tokens":10}}}\n\n'
        b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"text","text":""}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"partial"}}\n\n'
        b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n'
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, upstream_bytes))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    # Forwarded verbatim: the client sees the partial output and the error event.
    assert body == upstream_bytes

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())
    assert len(logs) == 1
    log = logs[0]
    assert log.account_id == "anthropic-account"
    assert log.status == "error"
    assert log.error_code == "stream_error_overloaded_error"
    assert log.error_message == "Overloaded"
    assert log.input_tokens == 10


@pytest.mark.asyncio
async def test_anthropic_count_tokens_forwards_verbatim_without_usage_writes(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session
        assert provider_name == "anthropic"
        captured["headers"] = dict(headers)
        captured["json_body"] = dict(json_body)
        return _FakeResponseContext(
            _FakeResponse(200, b'{"input_tokens": 2095}', headers={"content-type": "application/json"})
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    payload = {
        "model": "claude-fable-5",
        "system": "Claude Code system prompt prefix must pass through untouched.",
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "adaptive"},
    }
    response = await async_client.post(
        "/v1/messages/count_tokens",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "authorization": "Bearer client-token",
            "x-api-key": "client-placeholder",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 2095}
    assert captured["json_body"] == payload
    assert captured["headers"]["Authorization"] == "Bearer anthropic-access"
    assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert "x-api-key" not in {key.lower() for key in captured["headers"]}

    # Counting is quota-free: no request log, usage settlement, or cooldown
    # rows may be written for it.
    async with SessionLocal() as session:
        logs = list((await session.execute(select(RequestLog))).scalars())
        cooldowns = list((await session.execute(select(AdditionalUsageHistory))).scalars())

    assert logs == []
    assert cooldowns == []


@pytest.mark.asyncio
async def test_anthropic_count_tokens_passes_through_upstream_error_envelope(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    upstream_error = b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}'

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(429, upstream_error, headers={"content-type": "application/json"}))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-fable-5", "messages": [{"role": "user", "content": "hello"}]},
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    assert response.content == upstream_error

    # An upstream error on the free counting endpoint must not perturb
    # account health or quota cooldowns.
    async with SessionLocal() as session:
        account = (await session.execute(select(Account))).scalar_one()
        cooldowns = list((await session.execute(select(AdditionalUsageHistory))).scalars())

    assert account.status == AccountStatus.ACTIVE
    assert cooldowns == []


@pytest.mark.asyncio
async def test_anthropic_fast_mode_adds_required_oauth_and_fast_betas(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, json_body
        assert provider_name == "anthropic"
        captured["headers"] = dict(headers)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-opus-4-8",
        "max_tokens": 32,
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
        "speed": "fast",
    }
    async with async_client.stream("POST", "/v1/messages", json=payload, headers={}) as response:
        assert response.status_code == 200
        await response.aread()

    betas = {value.strip() for value in captured["headers"]["anthropic-beta"].split(",")}
    assert {"oauth-2025-04-20", "fast-mode-2026-02-01"}.issubset(betas)
    assert captured["headers"]["Authorization"] == "Bearer anthropic-access"


@pytest.mark.asyncio
async def test_glm_messages_selects_glm_account_and_upstream(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    await _insert_account(
        account_id="glm-account",
        provider="glm",
        access_token="glm-access",
        email="glm@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session
        captured["provider_name"] = provider_name
        captured["headers"] = dict(headers)
        captured["json_body"] = dict(json_body)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "glm-5.2",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert captured["provider_name"] == "glm"
    assert captured["headers"]["Authorization"] == "Bearer glm-access"
    assert captured["json_body"]["model"] == "glm-5.2"

    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()

    assert log.provider == "glm"
    assert log.account_id == "glm-account"
    assert log.status == "success"


ANTHROPIC_JSON_BYTES = (
    b'{"id":"msg_456","type":"message","role":"assistant","model":"claude-sonnet-4-20250514",'
    b'"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn",'
    b'"usage":{"input_tokens":12,"output_tokens":7,"cache_creation_input_tokens":2,"cache_read_input_tokens":1}}'
)


@pytest.mark.asyncio
async def test_anthropic_messages_non_streaming_logs_usage(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20", "x-api-key": "client-placeholder"},
    )
    assert response.status_code == 200
    assert response.content == ANTHROPIC_JSON_BYTES

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())

    assert len(logs) == 1
    log = logs[0]
    assert log.provider == "anthropic"
    assert log.status == "success"
    assert log.input_tokens == 12
    assert log.output_tokens == 7
    assert log.cache_creation_tokens == 2
    assert log.cache_read_tokens == 1
    assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_anthropic_messages_non_streaming_upstream_529_returns_native_error(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-overloaded",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(
                529,
                b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            )
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 529
    assert response.json() == {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }

    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()

    assert log.status == "error"
    assert log.error_code == "upstream_529"
    assert log.error_message == "Overloaded"


@pytest.mark.asyncio
async def test_anthropic_messages_passes_primary_reset_preference_to_selector(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-primary-reset",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return SimpleNamespace(prefer_earlier_reset_accounts=True)

    seen_selection_kwargs: list[dict[str, Any]] = []

    async def fake_select_account(self, **kwargs):
        del self
        seen_selection_kwargs.append(kwargs)
        async with SessionLocal() as session:
            account = await session.get(Account, "anthropic-primary-reset")
        return AccountSelection(account=account, error_message=None)

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(anthropic_proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)
    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert seen_selection_kwargs[0]["prefer_earlier_reset_accounts"] is True
    assert seen_selection_kwargs[0]["prefer_earlier_reset_window"] == "primary"
    assert seen_selection_kwargs[0]["routing_strategy"] == "usage_weighted"


@pytest.mark.asyncio
async def test_anthropic_messages_keep_same_session_on_sticky_account(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="claude-b@example.com",
    )

    seen_authorizations: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, json_body
        assert provider_name == "anthropic"
        seen_authorizations.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "keep me sticky"}],
        "thinking": {"type": "adaptive"},
    }
    for _ in range(2):
        response = await async_client.post(
            "/v1/messages",
            json=payload,
            headers={
                "anthropic-beta": "oauth-2025-04-20",
                "x-claude-session-id": "claude-session-sticky",
            },
        )
        assert response.status_code == 200

    assert len(seen_authorizations) == 2
    assert seen_authorizations[1] == seen_authorizations[0]

    request = AnthropicMessageRequest.model_validate(payload)
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-sticky"},
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic"),
    )
    async with SessionLocal() as session:
        result = await session.execute(
            select(StickySession).where(
                StickySession.key == sticky_key,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
        )
        sticky = result.scalar_one()

    expected_account_id = "anthropic-a" if seen_authorizations[0].endswith("anthropic-access-a") else "anthropic-b"
    assert sticky.account_id == expected_account_id


@pytest.mark.asyncio
async def test_anthropic_messages_fails_over_when_refresh_invalid_grant_before_upstream(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-stale",
        provider="anthropic",
        access_token="anthropic-access-stale",
        email="stale-claude@example.com",
    )
    await _insert_account(
        account_id="anthropic-healthy",
        provider="anthropic",
        access_token="anthropic-access-healthy",
        email="healthy-claude@example.com",
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "fail over stale auth"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-refresh-failover"},
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic"),
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-stale",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    async def fake_ensure_fresh(self, account, *, force=False):
        del self, force
        if account.id == "anthropic-stale":
            from app.modules.accounts.repository import AccountsRepository

            async with SessionLocal() as session:
                await AccountsRepository(session).update_status(
                    account.id, AccountStatus.REAUTH_REQUIRED, "Refresh token grant invalid - re-login required"
                )
            raise anthropic_proxy_module.RefreshError(
                "auth_refresh_invalid_grant",
                "Refresh token not found or invalid",
                False,
            )
        return account

    seen_authorizations: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        seen_authorizations.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(anthropic_proxy_module.AuthManager, "ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "x-claude-session-id": "claude-refresh-failover",
        },
    )

    assert response.status_code == 200
    assert seen_authorizations == ["Bearer anthropic-access-healthy"]

    async with SessionLocal() as session:
        accounts = {
            account.id: account
            for account in (
                await session.execute(select(Account).where(Account.id.in_(["anthropic-stale", "anthropic-healthy"])))
            )
            .scalars()
            .all()
        }
        logs = list((await session.execute(select(RequestLog).order_by(RequestLog.id))).scalars())
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert accounts["anthropic-stale"].status == AccountStatus.REAUTH_REQUIRED
    assert accounts["anthropic-stale"].deactivation_reason == "Refresh token grant invalid - re-login required"
    assert accounts["anthropic-healthy"].status == AccountStatus.ACTIVE
    assert [log.status for log in logs] == ["error", "success"]
    assert [log.account_id for log in logs] == ["anthropic-stale", "anthropic-healthy"]
    assert logs[0].error_code == "auth_refresh_invalid_grant"
    assert sticky.account_id == "anthropic-healthy"


@pytest.mark.asyncio
async def test_anthropic_429_records_quota_cooldown_and_fails_over_without_global_rate_limit(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="claude-b@example.com",
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "fail over high tier"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    quota_key = anthropic_proxy_module._anthropic_quota_key(request)
    affinity_quota_key = anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic")
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-failover"},
        quota_key=affinity_quota_key,
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-a",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    seen_authorizations: list[str] = []
    cooldown_reset_at = int((utcnow() + timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp())
    cooldown_reset_header = datetime.fromtimestamp(cooldown_reset_at, tz=timezone.utc).isoformat()

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, json_body
        assert provider_name == "anthropic"
        seen_authorizations.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer anthropic-access-a":
            return _FakeResponseContext(
                _FakeResponse(
                    429,
                    b'{"error":{"message":"top model cooldown"}}',
                    headers={
                        "anthropic-ratelimit-unified-reset": cooldown_reset_header,
                    },
                )
            )
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "x-claude-session-id": "claude-session-failover",
        },
    )

    assert response.status_code == 200
    assert seen_authorizations == ["Bearer anthropic-access-a", "Bearer anthropic-access-b"]

    async with SessionLocal() as session:
        accounts = {
            account.id: account
            for account in (
                await session.execute(select(Account).where(Account.id.in_(["anthropic-a", "anthropic-b"])))
            )
            .scalars()
            .all()
        }
        cooldowns = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(
                        AdditionalUsageHistory.quota_key == quota_key,
                        AdditionalUsageHistory.window == "primary",
                    )
                )
            )
            .scalars()
            .all()
        )
        logs = list((await session.execute(select(RequestLog).order_by(RequestLog.id))).scalars())
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert accounts["anthropic-a"].status == AccountStatus.ACTIVE
    assert accounts["anthropic-b"].status == AccountStatus.ACTIVE
    by_account = {entry.account_id: entry for entry in cooldowns}
    assert by_account["anthropic-a"].used_percent == 100.0
    assert by_account["anthropic-a"].reset_at == cooldown_reset_at
    assert by_account["anthropic-b"].used_percent == 0.0
    assert [log.status for log in logs] == ["error", "success"]
    assert [log.account_id for log in logs] == ["anthropic-a", "anthropic-b"]
    assert sticky.account_id == "anthropic-b"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    anthropic_a = next(
        account for account in accounts_response.json()["accounts"] if account["accountId"] == "anthropic-a"
    )
    cooldown = next(quota for quota in anthropic_a["additionalQuotas"] if quota["quotaKey"] == quota_key)
    assert cooldown["displayLabel"] == "Claude top models with thinking"
    assert cooldown["primaryWindow"]["usedPercent"] == 100.0


@pytest.mark.asyncio
async def test_anthropic_429_failover_repins_durable_mapping_to_serving_account(
    async_client,
    monkeypatch,
):
    """Reactive contract: after an in-request 429 fails over from the pinned
    account A to account B, the durable mapping must point at B so the next
    request reuses the cache B just rebuilt — and must not bounce back to the
    cooled-down A."""
    await _insert_account(
        account_id="anthropic-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="claude-b@example.com",
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "repin to serving account"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    quota_key = anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic")
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-repin"},
        quota_key=quota_key,
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-a",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    seen_authorizations: list[str] = []
    cooldown_reset_at = int((utcnow() + timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp())
    cooldown_reset_header = datetime.fromtimestamp(cooldown_reset_at, tz=timezone.utc).isoformat()

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        seen_authorizations.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer anthropic-access-a":
            return _FakeResponseContext(
                _FakeResponse(
                    429,
                    b'{"error":{"message":"top model cooldown"}}',
                    headers={"anthropic-ratelimit-unified-reset": cooldown_reset_header},
                )
            )
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    headers = {"anthropic-beta": "oauth-2025-04-20", "x-claude-session-id": "claude-session-repin"}
    first = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert first.status_code == 200
    assert seen_authorizations == ["Bearer anthropic-access-a", "Bearer anthropic-access-b"]

    async def _durable_mapping() -> str:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(StickySession).where(
                        StickySession.key == sticky_key,
                        StickySession.kind == StickySessionKind.CODEX_SESSION,
                    )
                )
            ).scalar_one()
            return row.account_id

    assert await _durable_mapping() == "anthropic-b"

    # Next request: A is still cooling down and the mapping is B, so the session
    # serves from B directly — no re-selection churn back to A.
    second = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert second.status_code == 200
    assert seen_authorizations == [
        "Bearer anthropic-access-a",
        "Bearer anthropic-access-b",
        "Bearer anthropic-access-b",
    ]
    assert await _durable_mapping() == "anthropic-b"


@pytest.mark.asyncio
async def test_anthropic_fast_429_records_fast_cooldown_and_allows_standard_fallback(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-fast-a",
        provider="anthropic",
        access_token="anthropic-fast-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-fast-b",
        provider="anthropic",
        access_token="anthropic-fast-access-b",
        email="claude-b@example.com",
    )

    session_id = "claude-fast-fallback"
    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "fast then fallback"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    affinity_quota_key = anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic")
    sticky_key = anthropic_proxy_module._messages_sticky_key(
        request,
        {"x-claude-session-id": session_id},
        provider_name="anthropic",
        quota_key=affinity_quota_key,
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-fast-a",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    seen: list[tuple[str, str | None]] = []
    cooldown_reset_at = int((utcnow() + timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp())
    cooldown_reset_header = datetime.fromtimestamp(cooldown_reset_at, tz=timezone.utc).isoformat()

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session
        assert provider_name == "anthropic"
        authorization = headers["Authorization"]
        speed = json_body.get("speed")
        seen.append((authorization, speed if isinstance(speed, str) else None))
        if authorization == "Bearer anthropic-fast-access-a" and speed == "fast":
            return _FakeResponseContext(
                _FakeResponse(
                    429,
                    b'{"error":{"message":"fast pool exhausted"}}',
                    headers={
                        "anthropic-fast-input-tokens-reset": cooldown_reset_header,
                    },
                )
            )
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    fast_response = await async_client.post(
        "/v1/messages",
        json={**payload, "speed": "fast"},
        headers={
            "anthropic-beta": "oauth-2025-04-20,fast-mode-2026-02-01",
            "x-claude-session-id": session_id,
        },
    )
    fallback_response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20,fast-mode-2026-02-01",
            "x-claude-session-id": session_id,
        },
    )

    assert fast_response.status_code == 200
    assert fallback_response.status_code == 200
    assert seen == [
        ("Bearer anthropic-fast-access-a", "fast"),
        ("Bearer anthropic-fast-access-b", "fast"),
        ("Bearer anthropic-fast-access-b", None),
    ]

    async with SessionLocal() as session:
        fast_cooldowns = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(
                        AdditionalUsageHistory.quota_key == "anthropic_fast",
                        AdditionalUsageHistory.window == "primary",
                    )
                )
            )
            .scalars()
            .all()
        )
        model_cooldowns = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(
                        AdditionalUsageHistory.quota_key == "anthropic_top_thinking",
                        AdditionalUsageHistory.window == "primary",
                    )
                )
            )
            .scalars()
            .all()
        )
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    by_account = {entry.account_id: entry for entry in fast_cooldowns}
    assert by_account["anthropic-fast-a"].used_percent == 100.0
    assert by_account["anthropic-fast-a"].reset_at == cooldown_reset_at
    assert by_account["anthropic-fast-b"].used_percent == 0.0
    assert all(entry.used_percent == 0.0 for entry in model_cooldowns)
    assert sticky.account_id == "anthropic-fast-b"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    anthropic_a = next(
        account for account in accounts_response.json()["accounts"] if account["accountId"] == "anthropic-fast-a"
    )
    fast_quota = next(quota for quota in anthropic_a["additionalQuotas"] if quota["quotaKey"] == "anthropic_fast")
    assert fast_quota["displayLabel"] == "Claude fast mode"
    assert fast_quota["primaryWindow"]["usedPercent"] == 100.0


async def _insert_weekly_usage(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None = None,
) -> None:
    from app.db.models import UsageHistory

    effective_reset_at = reset_at or int(
        (utcnow() + timedelta(days=5)).replace(tzinfo=timezone.utc).timestamp()
    )
    async with SessionLocal() as session:
        session.add(
            UsageHistory(
                account_id=account_id,
                provider="anthropic",
                window="secondary",
                used_percent=used_percent,
                reset_at=effective_reset_at,
                window_minutes=10080,
                recorded_at=utcnow(),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_fable_requests_route_to_accounts_under_weekly_threshold(async_client):
    await _insert_account(
        account_id="anthropic-weekly-hot",
        provider="anthropic",
        access_token="anthropic-access-hot",
        email="hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-weekly-fresh",
        provider="anthropic",
        access_token="anthropic-access-fresh",
        email="fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-weekly-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-weekly-fresh", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-threshold",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-weekly-fresh"


@pytest.mark.asyncio
async def test_fable_requests_fall_back_when_all_accounts_over_threshold(async_client):
    for index, used in enumerate((70.0, 90.0)):
        await _insert_account(
            account_id=f"anthropic-weekly-over-{index}",
            provider="anthropic",
            access_token=f"anthropic-access-over-{index}",
            email=f"over-{index}@example.com",
        )
        await _insert_weekly_usage(account_id=f"anthropic-weekly-over-{index}", used_percent=used)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-fallback",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    # Local threshold is a preference, not an oracle: the request still routes.
    assert response.status_code == 200
    assert response.json()["accountId"].startswith("anthropic-weekly-over-")


@pytest.mark.asyncio
async def test_non_fable_requests_prefer_accounts_over_weekly_threshold(async_client):
    await _insert_account(
        account_id="anthropic-burn-hot",
        provider="anthropic",
        access_token="anthropic-access-burn-hot",
        email="burn-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-burn-fresh",
        provider="anthropic",
        access_token="anthropic-access-burn-fresh",
        email="burn-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-burn-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-burn-fresh", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-burn",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    # usage_weighted alone would pick the fresh account; the burn_first stamp
    # on the over-threshold account must win so Fable headroom is preserved.
    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-burn-hot"


@pytest.mark.asyncio
async def test_non_fable_burn_preference_respects_preserve_policy(async_client):
    await _insert_account(
        account_id="anthropic-preserve-hot",
        provider="anthropic",
        access_token="anthropic-access-preserve-hot",
        email="preserve-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-preserve-fresh",
        provider="anthropic",
        access_token="anthropic-access-preserve-fresh",
        email="preserve-fresh@example.com",
    )
    async with SessionLocal() as session:
        account = await session.get(Account, "anthropic-preserve-hot")
        account.routing_policy = "preserve"
        await session.commit()
    await _insert_weekly_usage(account_id="anthropic-preserve-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-preserve-fresh", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-preserve",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    # The stored preserve policy must not be overridden by the burn stamp.
    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-preserve-fresh"


@pytest.mark.asyncio
async def test_mixed_model_session_holds_separate_pins(async_client):
    """A session that interleaves Fable and non-Fable requests on the same
    quotaKey family must hold two independent sticky pins (one per Fable-
    class affinity family) instead of ping-ponging a single shared pin
    between the under-threshold and over-threshold accounts."""
    await _insert_account(
        account_id="anthropic-mixed-hot",
        provider="anthropic",
        access_token="anthropic-access-mixed-hot",
        email="mixed-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-mixed-fresh",
        provider="anthropic",
        access_token="anthropic-access-mixed-fresh",
        email="mixed-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-mixed-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-mixed-fresh", used_percent=10.0)

    session_id = "session-mixed-model-alternation"

    async def _route(model: str) -> str:
        response = await async_client.post(
            "/api/anthropic/session-route",
            json={
                "sessionId": session_id,
                "model": model,
                "quotaKey": "anthropic_top_thinking",
            },
        )
        assert response.status_code == 200
        return response.json()["accountId"]

    assert await _route("claude-fable-5") == "anthropic-mixed-fresh"
    assert await _route("claude-sonnet-5") == "anthropic-mixed-hot"
    assert await _route("claude-fable-5") == "anthropic-mixed-fresh"
    assert await _route("claude-sonnet-5") == "anthropic-mixed-hot"

    session_hash = anthropic_proxy_module._hash_for_key(session_id)
    fable_sticky_key = f"claude:anthropic_top_thinking_fable:session:{session_hash}"
    non_fable_sticky_key = f"claude:anthropic_top_thinking:session:{session_hash}"
    assert fable_sticky_key != non_fable_sticky_key

    async with SessionLocal() as session:
        fable_sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == fable_sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
        non_fable_sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == non_fable_sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert fable_sticky.account_id == "anthropic-mixed-fresh"
    assert non_fable_sticky.account_id == "anthropic-mixed-hot"


@pytest.mark.asyncio
async def test_non_fable_sticky_session_drains_to_over_threshold_account(async_client):
    await _insert_account(
        account_id="anthropic-drain-hot",
        provider="anthropic",
        access_token="anthropic-access-drain-hot",
        email="drain-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-drain-fresh",
        provider="anthropic",
        access_token="anthropic-access-drain-fresh",
        email="drain-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-drain-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-drain-fresh", used_percent=10.0)

    session_id = "session-haiku-sticky-drain"
    sticky_key = "claude:anthropic_standard:session:" + anthropic_proxy_module._hash_for_key(session_id)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-drain-fresh",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": session_id,
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    # burn_first_sticky_drain must proactively move the pin off the fresh
    # account onto the over-threshold account without waiting for budget
    # pressure on the pinned account itself.
    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-drain-hot"

    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert sticky.account_id == "anthropic-drain-hot"


@pytest.mark.asyncio
async def test_non_fable_sticky_drain_disabled_keeps_pin(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_FABLE_STICKY_DRAIN_ENABLED", "false")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-drain-disabled-hot",
        provider="anthropic",
        access_token="anthropic-access-drain-disabled-hot",
        email="drain-disabled-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-drain-disabled-fresh",
        provider="anthropic",
        access_token="anthropic-access-drain-disabled-fresh",
        email="drain-disabled-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-drain-disabled-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-drain-disabled-fresh", used_percent=10.0)

    session_id = "session-haiku-sticky-drain-disabled"
    sticky_key = "claude:anthropic_standard:session:" + anthropic_proxy_module._hash_for_key(session_id)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-drain-disabled-fresh",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": session_id,
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-drain-disabled-fresh"

    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert sticky.account_id == "anthropic-drain-disabled-fresh"


@pytest.mark.asyncio
async def test_non_fable_sticky_session_already_on_drain_account_keeps_pin(async_client):
    await _insert_account(
        account_id="anthropic-drain-already-hot",
        provider="anthropic",
        access_token="anthropic-access-drain-already-hot",
        email="drain-already-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-drain-already-fresh",
        provider="anthropic",
        access_token="anthropic-access-drain-already-fresh",
        email="drain-already-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-drain-already-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-drain-already-fresh", used_percent=10.0)

    session_id = "session-haiku-sticky-drain-already"
    sticky_key = "claude:anthropic_standard:session:" + anthropic_proxy_module._hash_for_key(session_id)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-drain-already-hot",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": session_id,
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-drain-already-hot"

    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert sticky.account_id == "anthropic-drain-already-hot"


async def _insert_fable_access_marker(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            AdditionalUsageHistory(
                account_id=account_id,
                quota_key="anthropic_fable_access",
                limit_name="anthropic_fable_access",
                metered_feature="anthropic_fable_probe",
                window="primary",
                used_percent=used_percent,
                reset_at=reset_at,
                window_minutes=None,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_over_threshold_account_with_fresh_capable_marker_joins_fable_pool(async_client):
    await _insert_account(
        account_id="anthropic-fable-probe-capable",
        provider="anthropic",
        access_token="anthropic-access-fable-probe-capable",
        email="fable-probe-capable@example.com",
    )
    await _insert_account(
        account_id="anthropic-fable-probe-unmarked",
        provider="anthropic",
        access_token="anthropic-access-fable-probe-unmarked",
        email="fable-probe-unmarked@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-fable-probe-capable", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-fable-probe-unmarked", used_percent=60.0)
    fresh_reset_at = int((utcnow() + timedelta(hours=6)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-capable",
        used_percent=0.0,
        reset_at=fresh_reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-probe-capable",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    # Both accounts are over the weekly threshold; only the one with a fresh
    # capable Fable-access marker is admitted — proves marker-based
    # admission, not the blanket "all over threshold" fallback.
    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-probe-capable"


@pytest.mark.asyncio
async def test_over_threshold_accounts_without_fresh_capable_marker_stay_excluded_from_fable_pool(async_client):
    for account_id in (
        "anthropic-fable-probe-control",
        "anthropic-fable-probe-refused",
        "anthropic-fable-probe-stale",
        "anthropic-fable-probe-nomarker",
    ):
        await _insert_account(
            account_id=account_id,
            provider="anthropic",
            access_token=f"anthropic-access-{account_id}",
            email=f"{account_id}@example.com",
        )
        await _insert_weekly_usage(account_id=account_id, used_percent=60.0)

    future_reset_at = int((utcnow() + timedelta(hours=6)).replace(tzinfo=timezone.utc).timestamp())
    past_reset_at = int((utcnow() - timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-control",
        used_percent=0.0,
        reset_at=future_reset_at,
    )
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-refused",
        used_percent=100.0,
        reset_at=future_reset_at,
    )
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-stale",
        used_percent=0.0,
        reset_at=past_reset_at,
    )
    # anthropic-fable-probe-nomarker gets no marker at all.

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-probe-exclusions",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    # The control account (fresh capable marker) is the only admitted
    # candidate; refused, expired-capable, and marker-less accounts stay
    # excluded even though all four are over the weekly threshold.
    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-probe-control"


@pytest.mark.asyncio
async def test_non_fable_burn_preference_unchanged_by_capable_fable_marker(async_client):
    await _insert_account(
        account_id="anthropic-burn-marker-hot",
        provider="anthropic",
        access_token="anthropic-access-burn-marker-hot",
        email="burn-marker-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-burn-marker-fresh",
        provider="anthropic",
        access_token="anthropic-access-burn-marker-fresh",
        email="burn-marker-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-burn-marker-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-burn-marker-fresh", used_percent=10.0)
    fresh_reset_at = int((utcnow() + timedelta(hours=6)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_access_marker(
        account_id="anthropic-burn-marker-hot",
        used_percent=0.0,
        reset_at=fresh_reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-burn-marker",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    # A capable Fable-access marker must not change non-Fable burn_first
    # preference: the over-threshold account still drains non-Fable traffic.
    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-burn-marker-hot"


async def _insert_fable_scoped_weekly(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None = None,
    recorded_at: datetime | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            AdditionalUsageHistory(
                account_id=account_id,
                quota_key="anthropic_fable_scoped_weekly",
                limit_name="anthropic_fable_scoped_weekly",
                metered_feature="anthropic_fable_scoped_weekly",
                window="primary",
                used_percent=used_percent,
                reset_at=reset_at,
                window_minutes=10080,
                recorded_at=recorded_at or utcnow(),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_fable_scoped_exhaustion_excludes_despite_overall_headroom(async_client):
    # OpenSpec scenario: 30% overall weekly (well under the 50% heuristic)
    # but the Fable-scoped signal is at the scoped threshold (default 100%)
    # — the scoped signal must exclude the account even though the overall
    # heuristic alone would have admitted it. A second, genuinely-eligible
    # control account rules out the "all over threshold" full-pool fallback
    # masking the exclusion.
    await _insert_account(
        account_id="anthropic-scoped-exhausted",
        provider="anthropic",
        access_token="anthropic-access-scoped-exhausted",
        email="scoped-exhausted@example.com",
    )
    await _insert_account(
        account_id="anthropic-scoped-control",
        provider="anthropic",
        access_token="anthropic-access-scoped-control",
        email="scoped-control@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-scoped-exhausted", used_percent=30.0)
    await _insert_weekly_usage(account_id="anthropic-scoped-control", used_percent=10.0)
    scoped_reset_at = int((utcnow() + timedelta(days=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_scoped_weekly(
        account_id="anthropic-scoped-exhausted",
        used_percent=100.0,
        reset_at=scoped_reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-scoped-exhausted",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-scoped-control"


@pytest.mark.asyncio
async def test_fable_scoped_exhaustion_with_elapsed_reset_remains_in_model_scope(
    async_client,
    monkeypatch,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        provider="anthropic",
        access_token="anthropic-access-elapsed-scoped-headroom",
        email="elapsed-scoped-headroom@example.com",
    )
    await _insert_account(
        account_id="anthropic-fable-elapsed-scoped-paid",
        provider="anthropic",
        access_token="anthropic-access-elapsed-scoped-paid",
        email="elapsed-scoped-paid@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    now = utcnow()
    past_reset_at = int((now - timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    paid_reset_at = int((now + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        used_percent=10.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        used_percent=10.0,
    )
    await _insert_fable_scoped_weekly(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        used_percent=100.0,
        reset_at=past_reset_at,
    )
    await _insert_primary_usage(
        account_id="anthropic-fable-elapsed-scoped-paid",
        used_percent=100.0,
        reset_at=paid_reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-fable-elapsed-scoped-paid",
        used_percent=20.0,
    )
    await _insert_fable_scoped_weekly(
        account_id="anthropic-fable-elapsed-scoped-paid",
        used_percent=20.0,
        reset_at=paid_reset_at,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-fable-elapsed-scoped-paid",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=paid_reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )

    assert eligibility.account_ids == ["anthropic-fable-elapsed-scoped-headroom"]
    assert eligibility.paid_fallback_account_ids == frozenset()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-elapsed-scoped-headroom",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-elapsed-scoped-headroom"


@pytest.mark.asyncio
async def test_all_active_fable_scoped_exhaustions_return_earliest_reset(
    async_client,
    monkeypatch,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    now = utcnow()
    earliest_reset = int((now + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    later_reset = earliest_reset + 3600
    for account_id, reset_at in (
        ("anthropic-fable-hard-excluded-a", earliest_reset),
        ("anthropic-fable-hard-excluded-b", later_reset),
    ):
        await _insert_account(
            account_id=account_id,
            provider="anthropic",
            access_token=f"anthropic-access-{account_id}",
            email=f"{account_id}@example.com",
        )
        await _insert_primary_usage(account_id=account_id, used_percent=10.0)
        await _insert_weekly_usage(account_id=account_id, used_percent=10.0)
        await _insert_fable_scoped_weekly(
            account_id=account_id,
            used_percent=100.0,
            reset_at=reset_at,
        )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == []
    assert eligibility.blocked_count == 2
    assert eligibility.next_reset_at == earliest_reset
    assert eligibility.paid_fallback_account_ids == frozenset()

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(earliest_reset)
    assert upstream_calls == []


@pytest.mark.asyncio
async def test_fable_scoped_headroom_admits_despite_overall_heuristic(async_client):
    # OpenSpec scenario: 62% overall weekly (over the 50% heuristic, which
    # alone would require a probe marker to admit) but only 45%
    # Fable-scoped — the scoped signal alone admits the account, with no
    # probe marker present.
    await _insert_account(
        account_id="anthropic-scoped-headroom",
        provider="anthropic",
        access_token="anthropic-access-scoped-headroom",
        email="scoped-headroom@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-scoped-headroom", used_percent=62.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-scoped-headroom", used_percent=45.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-scoped-headroom",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-scoped-headroom"


@pytest.mark.asyncio
async def test_non_fable_burn_set_derived_from_scoped_percents_when_fresh(async_client):
    # Inverted relative to the overall-weekly heuristic: the scoped-hot
    # account has LOW overall weekly usage (heuristic would spare it) but a
    # scoped-exhausted marker; the scoped-cool account has HIGH overall
    # weekly usage (heuristic would burn it) but scoped headroom. The burn
    # set must follow the scoped percents, not the heuristic.
    await _insert_account(
        account_id="anthropic-burn-scoped-hot",
        provider="anthropic",
        access_token="anthropic-access-burn-scoped-hot",
        email="burn-scoped-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-burn-scoped-cool",
        provider="anthropic",
        access_token="anthropic-access-burn-scoped-cool",
        email="burn-scoped-cool@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-burn-scoped-hot", used_percent=30.0)
    await _insert_weekly_usage(account_id="anthropic-burn-scoped-cool", used_percent=62.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-burn-scoped-hot", used_percent=100.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-burn-scoped-cool", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-burn-scoped",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-burn-scoped-hot"


@pytest.mark.asyncio
async def test_stale_fable_scoped_row_falls_back_to_overall_heuristic(async_client):
    await _insert_account(
        account_id="anthropic-scoped-stale",
        provider="anthropic",
        access_token="anthropic-access-scoped-stale",
        email="scoped-stale@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-scoped-stale", used_percent=10.0)
    stale_recorded_at = utcnow() - timedelta(hours=7)
    # Scoped-exhausted, but recorded over 6h ago — must be ignored in favor
    # of the (well under threshold) overall-weekly heuristic.
    await _insert_fable_scoped_weekly(
        account_id="anthropic-scoped-stale",
        used_percent=100.0,
        recorded_at=stale_recorded_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-scoped-stale",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-scoped-stale"


async def _insert_primary_usage(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None = None,
    credits_has: bool | None = None,
    credits_unlimited: bool | None = None,
    credits_balance: float | None = None,
) -> None:
    from app.db.models import UsageHistory

    effective_reset = (
        reset_at
        if reset_at is not None
        else int((utcnow() + timedelta(hours=3)).replace(tzinfo=timezone.utc).timestamp())
    )
    async with SessionLocal() as session:
        session.add(
            UsageHistory(
                account_id=account_id,
                provider="anthropic",
                window="primary",
                used_percent=used_percent,
                reset_at=effective_reset,
                window_minutes=300,
                recorded_at=utcnow(),
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_session_route_admits_recovered_primary_despite_historical_tripwire(async_client):
    """The public routing surface must use current subscription headroom
    instead of quarantining an account on an older paid-usage marker."""
    await _insert_account(
        account_id="anthropic-tripwire-recovered",
        provider="anthropic",
        access_token="anthropic-access-tripwire-recovered",
        email="tripwire-recovered@example.com",
    )
    future_reset = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-tripwire-recovered",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=future_reset,
        recorded_at=utcnow() - timedelta(minutes=5),
    )
    await _insert_primary_usage(
        account_id="anthropic-tripwire-recovered",
        used_percent=0.0,
        reset_at=future_reset,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-tripwire-recovered",
            "model": "claude-sonnet-4-6",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-tripwire-recovered"


@pytest.mark.asyncio
async def test_exhausted_primary_window_account_is_not_selected_when_alternative_exists(async_client, monkeypatch):
    """An account at 100% primary utilization (extra usage keeps upstream at
    200) must never serve pool traffic while a subscription-covered account
    exists."""
    await _insert_account(
        account_id="anthropic-exhausted",
        provider="anthropic",
        access_token="anthropic-access-exhausted",
        email="exhausted@example.com",
    )
    await _insert_account(
        account_id="anthropic-headroom",
        provider="anthropic",
        access_token="anthropic-access-headroom",
        email="headroom@example.com",
    )
    exhausted_reset = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted",
        used_percent=100.0,
        reset_at=exhausted_reset,
        credits_has=True,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=exhausted_reset,
    )
    await _insert_primary_usage(account_id="anthropic-headroom", used_percent=10.0)

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        captured["authorization"] = headers["Authorization"]
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer anthropic-access-headroom"


@pytest.mark.asyncio
async def test_pool_of_exhausted_windows_returns_rate_limit_envelope_by_default(async_client, monkeypatch):
    """ANTHROPIC_ROUTE_TO_EXTRA_USAGE defaults to false: pool-wide primary
    exhaustion surfaces the 429 + earliest reset envelope and no request is
    forwarded to a credit-billing account."""
    await _insert_account(
        account_id="anthropic-exhausted-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="a@example.com",
    )
    await _insert_account(
        account_id="anthropic-exhausted-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="b@example.com",
    )
    earliest_reset = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted-a",
        used_percent=100.0,
        reset_at=earliest_reset,
        credits_has=True,
        credits_balance=50.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-a",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=earliest_reset,
    )
    await _insert_primary_usage(
        account_id="anthropic-exhausted-b",
        used_percent=100.0,
        reset_at=earliest_reset + 3600,
    )

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["error"]["type"] == "rate_limit_error"
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(earliest_reset)
    assert upstream_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credits_has", "credits_unlimited", "credits_balance"),
    [(True, False, 186.6), (False, True, None)],
)
async def test_route_to_extra_usage_opt_in_serves_as_last_resort(
    async_client,
    monkeypatch,
    credits_has,
    credits_unlimited,
    credits_balance,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-exhausted-only",
        provider="anthropic",
        access_token="anthropic-access-exhausted",
        email="exhausted@example.com",
    )
    await _insert_account(
        account_id="anthropic-exhausted-without-credits",
        provider="anthropic",
        access_token="anthropic-access-without-credits",
        email="without-credits@example.com",
    )
    paid_reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    blocked_reset_at = paid_reset_at + 3600
    await _insert_primary_usage(
        account_id="anthropic-exhausted-only",
        used_percent=100.0,
        reset_at=paid_reset_at,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )
    await _insert_primary_usage(
        account_id="anthropic-exhausted-without-credits",
        used_percent=100.0,
        reset_at=blocked_reset_at,
        credits_has=False,
        credits_balance=0.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-only",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=paid_reset_at,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-without-credits",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=blocked_reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == ["anthropic-exhausted-only"]
    assert eligibility.blocked_count == 1
    assert eligibility.next_reset_at == blocked_reset_at
    assert eligibility.paid_fallback_account_ids == frozenset({"anthropic-exhausted-only"})

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert upstream_calls == ["Bearer anthropic-access-exhausted"]


@pytest.mark.asyncio
async def test_fable_paid_fallback_uses_model_scope_and_request_scoped_status_bypass(
    async_client,
    monkeypatch,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    headroom_ids = [f"anthropic-fable-scoped-exhausted-{index}" for index in range(3)]
    hard_exclusion_reset_at = int((utcnow() + timedelta(days=2)).replace(tzinfo=timezone.utc).timestamp())
    for account_id in headroom_ids:
        await _insert_account(
            account_id=account_id,
            provider="anthropic",
            access_token=f"anthropic-access-{account_id}",
            email=f"{account_id}@example.com",
        )
        await _insert_primary_usage(account_id=account_id, used_percent=10.0)
        await _insert_weekly_usage(account_id=account_id, used_percent=10.0)
        await _insert_fable_scoped_weekly(
            account_id=account_id,
            used_percent=100.0,
            reset_at=hard_exclusion_reset_at,
        )

    await _insert_account(
        account_id="anthropic-fable-paid",
        provider="anthropic",
        access_token="anthropic-access-fable-paid",
        email="fable-paid@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    await _insert_account(
        account_id="anthropic-fable-exhausted-no-credits",
        provider="anthropic",
        access_token="anthropic-access-fable-exhausted-no-credits",
        email="fable-exhausted-no-credits@example.com",
    )
    paid_reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    no_credit_reset_at = paid_reset_at + 1800
    await _insert_primary_usage(
        account_id="anthropic-fable-paid",
        used_percent=100.0,
        reset_at=paid_reset_at,
        credits_has=True,
        credits_balance=250.0,
    )
    await _insert_primary_usage(
        account_id="anthropic-fable-exhausted-no-credits",
        used_percent=100.0,
        reset_at=no_credit_reset_at,
        credits_has=False,
        credits_balance=0.0,
    )
    for account_id, reset_at in (
        ("anthropic-fable-paid", paid_reset_at),
        ("anthropic-fable-exhausted-no-credits", no_credit_reset_at),
    ):
        await _insert_weekly_usage(account_id=account_id, used_percent=20.0)
        await _insert_fable_scoped_weekly(account_id=account_id, used_percent=20.0)
        await _insert_quota_cooldown(
            account_id=account_id,
            quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
            reset_at=reset_at,
        )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == ["anthropic-fable-paid"]
    assert eligibility.paid_fallback_account_ids == frozenset({"anthropic-fable-paid"})
    assert eligibility.blocked_count == 1
    assert eligibility.next_reset_at == no_credit_reset_at

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-live-shaped-fable-paid-fallback",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-paid"
    async with SessionLocal() as session:
        paid_account = await session.get(Account, "anthropic-fable-paid")
        assert paid_account is not None
        assert paid_account.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_fable_soft_headroom_blocks_paid_fallback(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-fable-paid-preferred",
        provider="anthropic",
        access_token="anthropic-access-fable-paid-preferred",
        email="fable-paid-preferred@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    await _insert_account(
        account_id="anthropic-fable-soft-headroom",
        provider="anthropic",
        access_token="anthropic-access-fable-soft-headroom",
        email="fable-soft-headroom@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-fable-paid-preferred",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=250.0,
    )
    await _insert_weekly_usage(account_id="anthropic-fable-paid-preferred", used_percent=20.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-fable-paid-preferred", used_percent=20.0)
    await _insert_quota_cooldown(
        account_id="anthropic-fable-paid-preferred",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=reset_at,
    )
    await _insert_primary_usage(account_id="anthropic-fable-soft-headroom", used_percent=10.0)
    await _insert_weekly_usage(account_id="anthropic-fable-soft-headroom", used_percent=80.0)

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == ["anthropic-fable-soft-headroom"]
    assert eligibility.paid_fallback_account_ids == frozenset()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-soft-headroom-before-paid",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-soft-headroom"


@pytest.mark.asyncio
async def test_fable_secondary_exhausted_preference_does_not_hide_soft_headroom(
    async_client,
    monkeypatch,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-fable-preferred-weekly-exhausted",
        provider="anthropic",
        access_token="anthropic-access-fable-preferred-weekly-exhausted",
        email="fable-preferred-weekly-exhausted@example.com",
    )
    await _insert_account(
        account_id="anthropic-fable-soft-weekly-headroom",
        provider="anthropic",
        access_token="anthropic-access-fable-soft-weekly-headroom",
        email="fable-soft-weekly-headroom@example.com",
    )
    weekly_reset_at = int((utcnow() + timedelta(days=5)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-fable-preferred-weekly-exhausted",
        used_percent=10.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-fable-preferred-weekly-exhausted",
        used_percent=100.0,
        reset_at=weekly_reset_at,
    )
    await _insert_fable_scoped_weekly(
        account_id="anthropic-fable-preferred-weekly-exhausted",
        used_percent=20.0,
        reset_at=weekly_reset_at,
    )
    await _insert_primary_usage(
        account_id="anthropic-fable-soft-weekly-headroom",
        used_percent=10.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-fable-soft-weekly-headroom",
        used_percent=80.0,
        reset_at=weekly_reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == ["anthropic-fable-soft-weekly-headroom"]
    assert eligibility.blocked_count == 1
    assert eligibility.next_reset_at == weekly_reset_at
    assert eligibility.paid_fallback_account_ids == frozenset()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-soft-weekly-headroom",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-soft-weekly-headroom"


@pytest.mark.asyncio
async def test_route_to_extra_usage_paid_only_eligibility_has_no_blocked_reset(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-paid-only",
        provider="anthropic",
        access_token="anthropic-access-paid-only",
        email="paid-only@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-paid-only",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-paid-only",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )

    assert eligibility.account_ids == ["anthropic-paid-only"]
    assert eligibility.blocked_count == 0
    assert eligibility.next_reset_at is None
    assert eligibility.paid_fallback_account_ids == frozenset({"anthropic-paid-only"})


@pytest.mark.parametrize("credits_balance", [None, 0.0])
@pytest.mark.asyncio
async def test_route_to_extra_usage_requires_positive_finite_credit_balance(
    async_client,
    monkeypatch,
    credits_balance,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-exhausted-no-credits",
        provider="anthropic",
        access_token="anthropic-access-no-credits",
        email="no-credits@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted-no-credits",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=credits_balance,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-no-credits",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-no-extra-usage-credits",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "anthropic_quota_cooldown"


@pytest.mark.asyncio
async def test_route_to_extra_usage_does_not_bypass_real_request_quota_cooldown(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-exhausted-with-credits",
        provider="anthropic",
        access_token="anthropic-access-with-credits",
        email="with-credits@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted-with-credits",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-with-credits",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-model-cooldown",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "anthropic_quota_cooldown"


@pytest.mark.asyncio
async def test_route_to_extra_usage_does_not_bypass_secondary_exhaustion(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-primary-and-weekly-exhausted",
        provider="anthropic",
        access_token="anthropic-access-primary-and-weekly-exhausted",
        email="primary-and-weekly-exhausted@example.com",
    )
    primary_reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    secondary_reset_at = int((utcnow() + timedelta(days=5)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-primary-and-weekly-exhausted",
        used_percent=100.0,
        reset_at=primary_reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-primary-and-weekly-exhausted",
        used_percent=100.0,
        reset_at=secondary_reset_at,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-primary-and-weekly-exhausted",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=primary_reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(
        _proxy_repo_context
    )._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == []
    assert eligibility.paid_fallback_account_ids == frozenset()
    assert eligibility.next_reset_at == secondary_reset_at

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(secondary_reset_at)
    assert upstream_calls == []


@pytest.mark.asyncio
async def test_count_tokens_stays_exempt_from_message_quota_cooldowns(async_client, monkeypatch):
    """The extra-usage eligibility gate must not leak into the quota-free
    count_tokens path: message cooldowns and high (sub-100) primary usage
    leave counting untouched."""
    await _insert_account(
        account_id="anthropic-exhausted-count",
        provider="anthropic",
        access_token="anthropic-access-count",
        email="count@example.com",
    )
    await _insert_primary_usage(account_id="anthropic-exhausted-count", used_percent=99.0)
    cooldown_reset = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-count",
        quota_key="anthropic_top_thinking",
        reset_at=cooldown_reset,
    )

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(200, b'{"input_tokens": 12}', headers={"content-type": "application/json"})
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={
            "model": "claude-fable-5",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 12}


_OVERAGE_BILLING_HEADERS = {
    # Captured live 2026-07-01 from a 200 served by billing extra-usage credits.
    "anthropic-ratelimit-unified-status": "rejected",
    "anthropic-ratelimit-unified-5h-status": "rejected",
    "anthropic-ratelimit-unified-5h-utilization": "1.0",
    "anthropic-ratelimit-unified-7d-status": "allowed",
    "anthropic-ratelimit-unified-overage-status": "allowed",
    "anthropic-ratelimit-unified-overage-in-use": "true",
}


@pytest.mark.asyncio
async def test_credit_billing_response_trips_cooldown_and_rotates_next_request(async_client, monkeypatch):
    """First 200 served on extra-usage credits records the same cooldown a 429
    would, so the session's next request rotates instead of billing again."""
    await _insert_account(
        account_id="anthropic-tripwire",
        provider="anthropic",
        access_token="anthropic-access-tripwire",
        email="tripwire@example.com",
    )
    await _insert_account(
        account_id="anthropic-fallback",
        provider="anthropic",
        access_token="anthropic-access-fallback",
        email="fallback@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    serving_tokens: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        token = headers["Authorization"]
        serving_tokens.append(token)
        response_headers = {}
        if token == "Bearer anthropic-access-tripwire":
            response_headers = dict(_OVERAGE_BILLING_HEADERS)
            response_headers["anthropic-ratelimit-unified-reset"] = str(reset_at)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES, headers=response_headers))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "adaptive"},
    }
    headers = {
        "anthropic-beta": "oauth-2025-04-20",
        "x-claude-session-id": "tripwire-session",
    }
    # Pin the session to the soon-to-bill account so the first request
    # deterministically exercises the tripwire (mirrors a live session riding
    # an account across the 100% utilization boundary).
    request = AnthropicMessageRequest.model_validate(payload)
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        headers,
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic"),
    )
    assert sticky_key is not None
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-tripwire",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    first = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert first.status_code == 200
    assert serving_tokens[0] == "Bearer anthropic-access-tripwire"

    async with SessionLocal() as session:
        result = await session.execute(
            select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "anthropic-tripwire")
        )
        cooldown_rows = list(result.scalars())
    assert cooldown_rows, "tripwire must record a quota cooldown"
    latest = max(cooldown_rows, key=lambda row: row.recorded_at)
    assert latest.used_percent == 100.0
    assert latest.reset_at == reset_at
    assert latest.quota_key == anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY

    second = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert second.status_code == 200
    assert serving_tokens[-1] == "Bearer anthropic-access-fallback"


@pytest.mark.asyncio
async def test_pool_exhausted_streaming_request_waits_for_reset_and_serves(async_client, monkeypatch):
    """Agent session survives pool-wide exhaustion: the stream holds, the
    window resets, and the request is served on the freed account."""
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", "true")
    get_settings.cache_clear()
    await _insert_account(
        account_id="anthropic-waiting",
        provider="anthropic",
        access_token="anthropic-access-waiting",
        email="waiting@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=5)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-waiting",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    fake_now = {"value": 1_000_000.0}
    sleep_calls: list[float] = []

    def fake_clock() -> float:
        return fake_now["value"]

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        fake_now["value"] += delay
        # Simulate the upstream window resetting while the stream held open.
        async with SessionLocal() as session:
            result = await session.execute(
                select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "anthropic-waiting")
            )
            for row in result.scalars():
                row.reset_at = int((utcnow() - timedelta(seconds=1)).replace(tzinfo=timezone.utc).timestamp())
            await session.commit()

    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_CLOCK", fake_clock)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_SLEEP", fake_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_JITTER", lambda: 0.0)

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert sleep_calls, "the stream must hold before serving"
    text = body.decode("utf-8")
    assert "event: message_stop" in text
    assert "event: error" not in text


@pytest.mark.asyncio
async def test_pool_exhausted_wait_cap_expiry_emits_structured_rate_limit_error(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", "true")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_MAX_SECONDS", "400")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-capped",
        provider="anthropic",
        access_token="anthropic-access-capped",
        email="capped@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=3)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-capped",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    fake_now = {"value": 2_000_000.0}
    sleep_calls: list[float] = []

    def fake_clock() -> float:
        return fake_now["value"]

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        fake_now["value"] += delay

    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_CLOCK", fake_clock)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_SLEEP", fake_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_JITTER", lambda: 0.0)

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert sleep_calls, "the stream must hold before capping out"
    text = body.decode("utf-8")
    assert "event: error\n" in text
    error_data = json.loads(text.split("event: error\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert error_data["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_pool_exhausted_wait_defaults_to_immediate_envelope(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.delenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", raising=False)
    get_settings.cache_clear()
    await _insert_account(
        account_id="anthropic-no-wait",
        provider="anthropic",
        access_token="anthropic-access-no-wait",
        email="no-wait@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-no-wait",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["error"]["type"] == "rate_limit_error"
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(reset_at)


_OVERLOADED_529_BODY = b'{"error":{"type":"overloaded_error","message":"Overloaded"}}'


@pytest.mark.asyncio
async def test_upstream_529_fails_over_to_next_account(async_client, monkeypatch):
    """A transient 529 retries on another account instead of propagating, and
    records no quota cooldown for the browned-out account."""
    await _insert_account(
        account_id="anthropic-brownout",
        provider="anthropic",
        access_token="anthropic-access-brownout",
        email="brownout@example.com",
    )
    await _insert_account(
        account_id="anthropic-healthy-529",
        provider="anthropic",
        access_token="anthropic-access-healthy",
        email="healthy-529@example.com",
    )

    upstream_tokens: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        token = headers["Authorization"]
        upstream_tokens.append(token)
        if len(upstream_tokens) == 1:
            return _FakeResponseContext(_FakeResponse(529, _OVERLOADED_529_BODY))
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    backoff_sleeps: list[float] = []

    async def fake_backoff_sleep(delay: float) -> None:
        backoff_sleeps.append(delay)

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_SLEEP", fake_backoff_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_JITTER", lambda: 0.0)

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert len(upstream_tokens) == 2
    assert upstream_tokens[0] != upstream_tokens[1]
    assert backoff_sleeps == [pytest.approx(0.25)]

    async with SessionLocal() as session:
        brownout_cooldowns = list(
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "anthropic-brownout")
                )
            ).scalars()
        )
        logs = list((await session.execute(select(RequestLog))).scalars())

    # No cooldown for the browned-out account (the serving account writes its
    # normal 0% clear row on success, which is not a cooldown).
    assert brownout_cooldowns == []
    error_logs = [log for log in logs if log.status == "error"]
    assert [log.error_code for log in error_logs] == ["upstream_529"]
    assert any(log.status == "success" for log in logs)


@pytest.mark.asyncio
async def test_upstream_529_full_outage_fails_fast_with_overloaded_error(async_client, monkeypatch):
    for index in range(4):
        await _insert_account(
            account_id=f"anthropic-outage-{index}",
            provider="anthropic",
            access_token=f"anthropic-access-outage-{index}",
            email=f"outage-{index}@example.com",
        )

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(529, _OVERLOADED_529_BODY))

    backoff_sleeps: list[float] = []

    async def fake_backoff_sleep(delay: float) -> None:
        backoff_sleeps.append(delay)

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_SLEEP", fake_backoff_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_JITTER", lambda: 0.0)

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 529
    body = response.json()
    assert body["error"]["type"] == "overloaded_error"
    assert body["error"]["message"] == "Overloaded"
    # Bounded attempt budget: one call per attempt, distinct accounts, and no
    # backoff after the final attempt.
    assert len(upstream_calls) == 4
    assert len(set(upstream_calls)) == 4
    assert backoff_sleeps == [pytest.approx(0.25), pytest.approx(0.5), pytest.approx(1.0)]

    async with SessionLocal() as session:
        cooldowns = list((await session.execute(select(AdditionalUsageHistory))).scalars())
    assert cooldowns == []
