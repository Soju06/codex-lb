"""Tests for ``app.modules.claude.service.ClaudeProxyService``.

Phase 9 (Tasks 9.1, 9.2, 9.3, 9.4, 9.5) of the Claude OAuth pool. The
proxy service is the bridge between:

- the load balancer (account selection + cooldown bookkeeping),
- the chat client (passthrough ``POST /v1/messages``), and
- the auth manager (token decryption + rotate-and-retry on 401).

These tests exercise the business logic in isolation by stubbing each
collaborator; the SQLAlchemy repo is exercised in the integration suite.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
— requirements *Pooled proxy passthrough*, *401 from Anthropic triggers
rotate-and-retry once*, *Per-account refresh serialization (singleflight)*,
*Claude rate-limit cooldown mirrors Codex cooldown*, and *Streaming
passthrough*.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import pytest

from app.core.clients.anthropic.chat import StreamChunk
from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeRateLimited
from app.db.models import Account, AccountStatus
from app.modules.claude.auth_manager import (
    ClaudeAuthManager,
)
from app.modules.claude.service import (
    ClaudeProxyService,
    NoClaudeAccounts,
    ProviderScopeMismatch,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeChatClient:
    """Stub for ``ClaudeChatClient`` with manually-driven side effects."""

    def __init__(
        self,
        *,
        send_messages_return: Any = None,
        send_messages_side_effect: list[Any] | None = None,
    ) -> None:
        self.send_messages_return = send_messages_return
        self.send_messages_side_effect = send_messages_side_effect
        self.send_messages_calls: list[dict[str, Any]] = []
        self.stream_messages_call: list[dict[str, Any]] = []

    async def send_messages(
        self,
        *,
        access_token: str,
        request_body: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        self.send_messages_calls.append({"access_token": access_token, "request_body": request_body})
        if self.send_messages_side_effect is not None:
            value = self.send_messages_side_effect.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return self.send_messages_return

    async def stream_messages(
        self,
        *,
        access_token: str,
        request_body: Mapping[str, Any],
    ) -> Any:
        self.stream_messages_call.append({"access_token": access_token, "request_body": request_body})
        if isinstance(self.stream_messages_return, BaseException):
            raise self.stream_messages_return
        # ``stream_messages_return`` may be either a flat list of chunks
        # (single stream) OR a list of lists (sequence of streams; tests
        # for mid-stream retry consume one per call).
        if self.stream_messages_return and isinstance(self.stream_messages_return[0], list):
            next_batch = self.stream_messages_return.pop(0)
        else:
            next_batch = list(self.stream_messages_return)
            self.stream_messages_return = []
        return _StreamIterator(next_batch)

    stream_messages_return: list[Any] = []


class _StreamIterator:
    """Test double for ``_StreamingChatIterator``.

    Mirrors the real chat client's async-iterator surface: ``aclose``
    releases any in-flight resources (test stubs have none, so it's a
    no-op). The proxy service drives the iterator via ``async for``.

    Sentinel ``StreamChunk(kind="raise", data=exc)`` causes the iterator
    to raise the carried exception on the next ``__anext__`` so tests
    can simulate mid-stream 401s without subclassing.
    """

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = list(chunks)
        self._closed = False

    def __aiter__(self) -> "_StreamIterator":
        return self

    async def __anext__(self) -> StreamChunk:
        if not self._chunks:
            raise StopAsyncIteration
        chunk = self._chunks.pop(0)
        if chunk.kind == "raise":
            assert isinstance(chunk.data, BaseException)
            raise chunk.data
        return chunk

    async def aclose(self) -> None:
        self._closed = True


class _FakeLoadBalancer:
    """Stub for the load balancer proxy used by ClaudeProxyService.

    The proxy service calls four methods on the balancer: ``select_account``,
    ``record_claude_rate_limit_response``. The stub records every call so
    tests can assert ordering and arguments.
    """

    def __init__(self) -> None:
        self.choose: callable | None = None  # ty:ignore[invalid-type-form]
        self.select_calls: list[dict[str, Any]] = []
        self.record_calls: list[dict[str, Any]] = []
        self.record_health_calls: list[dict[str, Any]] = []

    async def select_account(
        self,
        *,
        provider: str,
        sticky_key: str | None = None,
        traffic_class: Any = None,
    ) -> Any:
        self.select_calls.append({"provider": provider, "sticky_key": sticky_key, "traffic_class": traffic_class})
        assert self.choose is not None
        selection = self.choose(
            provider=provider,
            sticky_key=sticky_key,
            traffic_class=traffic_class,
        )
        # Wrap in an AccountSelection-shaped object: service reads .account.
        return _Selection(selection)

    async def record_claude_rate_limit_response(
        self,
        *,
        account: Account,
        headers: Mapping[str, str],
        is_rate_limited_response: bool = True,
    ) -> None:
        self.record_calls.append(
            {
                "account_id": account.id,
                "headers": dict(headers),
                "is_rate_limited_response": is_rate_limited_response,
            }
        )

    async def record_error(self, account: Account) -> None:
        self.record_health_calls.append({"account_id": account.id})


class _Selection:
    def __init__(self, account: Account | None) -> None:
        self.account = account
        self.error_message: str | None = None
        self.error_code: str | None = None


class _FakeAccountsRepo:
    """Stub for ``AccountsRepository`` exposing the methods ClaudeProxyService
    uses: ``update_rate_limit_cache`` and ``update_last_used_at``.
    """

    def __init__(self) -> None:
        self.rate_limit_cache_writes: list[dict[str, Any]] = []
        self.last_used_at_updates: list[str] = []

    async def update_rate_limit_cache(self, account_id: str, fields: dict[str, object]) -> bool:
        self.rate_limit_cache_writes.append({"account_id": account_id, "fields": dict(fields)})
        return True

    async def update_last_used_at(self, account_id: str, *, at: datetime) -> bool:
        self.last_used_at_updates.append(account_id)
        return True


class _FakeRequestLogRepository:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def add_log(self, **kwargs: Any) -> Any:
        self.rows.append(dict(kwargs))
        return _LogRow(kwargs)


class _LogRow:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs


class _FakeAuthManager(ClaudeAuthManager):
    """Auth manager with stubbed internals so we don't touch the DB."""

    def __init__(self) -> None:
        # Skip ClaudeAuthManager.__init__ — we only need the public surface.
        self._tokens: dict[str, str] = {}
        self.rotate_calls: list[dict[str, Any]] = []
        self.get_token_calls: list[str] = []
        self.rotate_return: Any = _FakeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)

    async def get_access_token(self, account: Account) -> str:
        self.get_token_calls.append(account.id)
        return self._tokens.get(account.id, "AT")

    async def rotate_claude_access_token(
        self,
        account: Account,
    ) -> Any:
        self.rotate_calls.append({"account_id": account.id})
        return self.rotate_return


class _FakeRefreshResult:
    def __init__(self, *, access_token: str, refresh_token: str | None, expires_in: int) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in


def _make_account(account_id: str = "claude-1") -> Account:
    return Account(
        id=account_id,
        provider="claude",
        plan_type="claude_subscription",
        routing_policy="normal",
        access_token_encrypted=b"placeholder",
        refresh_token_encrypted=b"placeholder",
        id_token_encrypted=b"placeholder",
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        claude_account_uuid=account_id.removeprefix("claude-"),
        claude_refresh_token_encrypted=b"placeholder-rt",
        claude_access_token_encrypted=b"placeholder-at",
        claude_access_token_expires_at=datetime.now(tz=timezone.utc),
    )


def _make_api_key(provider_scope: str) -> Any:
    return _ApiKey(provider_scope=provider_scope)


class _ApiKey:
    def __init__(self, *, provider_scope: str) -> None:
        self.provider_scope = provider_scope


class _Deps:
    """Test-only bundle so fixtures stay readable."""

    def __init__(self) -> None:
        self.lb = _FakeLoadBalancer()
        self.chat = _FakeChatClient()
        self.auth = _FakeAuthManager()
        self.repo = _FakeAccountsRepo()
        self.logs = _FakeRequestLogRepository()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def deps() -> _Deps:
    return _Deps()


@pytest.fixture()
def proxy_service(deps: _Deps) -> ClaudeProxyService:
    return ClaudeProxyService(
        load_balancer=deps.lb,
        chat=deps.chat,
        auth_manager=deps.auth,
        accounts_repository=deps.repo,
        request_log_repository=deps.logs,
    )


# ---------------------------------------------------------------------------
# Task 9.1 — skeleton passthrough
# ---------------------------------------------------------------------------


async def test_happy_path_returns_body_and_headers(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    account = _make_account()
    deps.lb.choose = lambda **_: account
    body_in = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }
    deps.chat.send_messages_return = (
        {"id": "msg_01", "usage": {"input_tokens": 3, "output_tokens": 5}},
        {"anthropic-ratelimit-status": "allowed"},
    )

    out_body, out_headers = await proxy_service.stream_or_complete_messages(
        request_body=body_in,
        api_key=_make_api_key(provider_scope="claude"),
        request_id="r-1",
    )

    assert out_body == {"id": "msg_01", "usage": {"input_tokens": 3, "output_tokens": 5}}
    assert out_headers["anthropic-ratelimit-status"] == "allowed"


async def test_happy_path_writes_request_log_row(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    account = _make_account()
    deps.lb.choose = lambda **_: account
    deps.chat.send_messages_return = (
        {
            "id": "msg_01",
            "usage": {
                "input_tokens": 3,
                "output_tokens": 5,
                "cache_creation_input_tokens": 1,
            },
        },
        {},
    )

    await proxy_service.stream_or_complete_messages(
        request_body={"model": "claude-opus-4-8"},
        api_key=_make_api_key(provider_scope="claude"),
        request_id="r-log",
    )

    assert len(deps.logs.rows) == 1
    row = deps.logs.rows[0]
    assert row["provider"] == "claude"
    assert row["account_id"] == account.id
    assert row["model"] == "claude-opus-4-8"
    assert row["input_tokens"] == 3
    assert row["output_tokens"] == 5
    assert row["cached_input_tokens"] == 1
    assert row["request_id"] == "r-log"
    assert row["status"] == "success"


async def test_happy_path_persists_rate_limit_cache(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    account = _make_account()
    deps.lb.choose = lambda **_: account
    deps.chat.send_messages_return = (
        {"id": "msg_01"},
        {
            "anthropic-ratelimit-requests-remaining": "42",
            "anthropic-ratelimit-status": "allowed",
        },
    )

    await proxy_service.stream_or_complete_messages(
        request_body={"model": "claude-opus-4-8"},
        api_key=_make_api_key(provider_scope="claude"),
        request_id="r-cache",
    )

    assert len(deps.repo.rate_limit_cache_writes) == 1
    write = deps.repo.rate_limit_cache_writes[0]
    assert write["account_id"] == account.id
    assert write["fields"]["rate_limit_requests_remaining"] == 42
    assert write["fields"]["rate_limit_status"] == "allowed"


async def test_provider_scope_mismatch_raises(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    deps.lb.choose = lambda **_: _make_account()
    with pytest.raises(ProviderScopeMismatch):
        await proxy_service.stream_or_complete_messages(
            request_body={"x": 1},
            api_key=_make_api_key(provider_scope="codex"),
            request_id="r-scope",
        )
    # The chat client must not have been called when scope is wrong.
    assert deps.chat.send_messages_calls == []


async def test_empty_pool_raises_no_claude_accounts(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    deps.lb.choose = lambda **_: None
    with pytest.raises(NoClaudeAccounts):
        await proxy_service.stream_or_complete_messages(
            request_body={"x": 1},
            api_key=_make_api_key(provider_scope="claude"),
            request_id="r-empty",
        )


async def test_request_body_passed_verbatim_no_copy(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    """Identity invariant: the request body object is forwarded unchanged.

    The chat client MUST receive the exact dict the caller passed in — no
    shallow copy, no transformation. The proxy layer is a passthrough; the
    only consumer-side mutation (auth headers) is layered on top by the
    transport, not by re-shaping the body.
    """
    deps.lb.choose = lambda **_: _make_account()
    deps.chat.send_messages_return = ({"id": "msg_01"}, {})
    body_in = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hi"}],
    }

    await proxy_service.stream_or_complete_messages(
        request_body=body_in,
        api_key=_make_api_key(provider_scope="claude"),
        request_id="r-identity",
    )

    assert deps.chat.send_messages_calls, "chat client must have been invoked"
    sent_body = deps.chat.send_messages_calls[0]["request_body"]
    assert sent_body is body_in, "request body must be forwarded by identity"


# ---------------------------------------------------------------------------
# Task 9.2 — 401 rotate-and-retry
# ---------------------------------------------------------------------------


async def test_first_401_triggers_rotate_and_retry(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    account = _make_account()
    deps.lb.choose = lambda **_: account
    deps.chat.send_messages_side_effect = [
        ClaudeAuthError("anthropic 401"),
        ({"id": "msg_01"}, {}),
    ]
    deps.auth.rotate_return = _FakeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)

    out_body, _headers = await proxy_service.stream_or_complete_messages(
        request_body={"model": "claude-opus-4-8"},
        api_key=_make_api_key(provider_scope="claude"),
        request_id="r-retry",
    )

    assert out_body == {"id": "msg_01"}
    # rotate called once on the 401-retry path.
    assert len(deps.auth.rotate_calls) == 1
    assert deps.auth.rotate_calls[0]["account_id"] == account.id
    # chat called twice — first 401, then retry succeeds.
    assert len(deps.chat.send_messages_calls) == 2


async def test_two_consecutive_401s_propagate_and_mark_account_unhealthy(
    proxy_service: ClaudeProxyService, deps: _Deps
) -> None:
    account = _make_account()
    deps.lb.choose = lambda **_: account
    deps.chat.send_messages_side_effect = [
        ClaudeAuthError("anthropic 401"),
        ClaudeAuthError("anthropic 401"),
    ]
    deps.auth.rotate_return = _FakeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)

    with pytest.raises(ClaudeAuthError):
        await proxy_service.stream_or_complete_messages(
            request_body={"model": "claude-opus-4-8"},
            api_key=_make_api_key(provider_scope="claude"),
            request_id="r-double-401",
        )

    # Exactly one rotation attempt — second 401 short-circuits the retry.
    assert len(deps.auth.rotate_calls) == 1
    assert len(deps.chat.send_messages_calls) == 2
    # Account was marked unhealthy via the load balancer helper.
    assert any(call["account_id"] == account.id for call in deps.lb.record_health_calls)


# ---------------------------------------------------------------------------
# Task 9.3 — rate-limit headers + 429 handling
# ---------------------------------------------------------------------------


async def test_429_sets_cooldown_and_re_raises(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    account = _make_account()
    deps.lb.choose = lambda **_: account
    headers = {
        "anthropic-ratelimit-requests-remaining": "0",
        "anthropic-ratelimit-requests-reset": "2030-01-01T12:00:00Z",
        "anthropic-ratelimit-status": "rejected",
    }
    # The chat client attaches upstream headers to the ClaudeRateLimited
    # exception (see app/core/clients/anthropic/chat.py). Tests use the same
    # shape so the proxy service can extract them for cooldown/cache writes.
    deps.chat.send_messages_side_effect = [
        ClaudeRateLimited("anthropic 429", headers=headers),
    ]

    with pytest.raises(ClaudeRateLimited):
        await proxy_service.stream_or_complete_messages(
            request_body={"model": "claude-opus-4-8"},
            api_key=_make_api_key(provider_scope="claude"),
            request_id="r-429",
        )

    # Cooldown recorded via the load balancer helper with is_rate_limited_response=True.
    assert len(deps.lb.record_calls) == 1
    record = deps.lb.record_calls[0]
    assert record["account_id"] == account.id
    assert record["is_rate_limited_response"] is True
    assert record["headers"]["anthropic-ratelimit-status"] == "rejected"
    # Cache persisted with parsed columns.
    assert len(deps.repo.rate_limit_cache_writes) == 1
    fields = deps.repo.rate_limit_cache_writes[0]["fields"]
    assert fields["rate_limit_requests_remaining"] == 0
    assert fields["rate_limit_status"] == "rejected"


# ---------------------------------------------------------------------------
# Task 9.5 — streaming passthrough
# ---------------------------------------------------------------------------


async def test_streaming_passes_sse_bytes_through_verbatim(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    account = _make_account()
    deps.lb.choose = lambda **_: account
    sse_chunks = [
        b'event: message_start\r\ndata: {"type":"message_start"}\r\n\r\n',
        b'event: content_block_delta\r\ndata: {"delta":{"text":"hi"}}\r\n\r\n',
        b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n',
    ]
    deps.chat.stream_messages_return = [
        StreamChunk(kind="headers", data={"anthropic-ratelimit-status": "allowed"}),
        *[StreamChunk(kind="sse", data=c) for c in sse_chunks],
        StreamChunk(kind="usage", data={"input_tokens": 3, "output_tokens": 5}),
    ]

    received: list[StreamChunk] = []
    async for chunk in proxy_service.stream_messages(
        request_body={"model": "claude-opus-4-8", "stream": True},
        api_key=_make_api_key(provider_scope="claude"),
        request_id="r-stream",
    ):
        received.append(chunk)

    # Forward verbatim — every sse chunk preserved byte-for-byte.
    forwarded_sse = [c.data for c in received if c.kind == "sse"]
    assert forwarded_sse == sse_chunks

    # One log row written exactly once after the stream completes.
    assert len(deps.logs.rows) == 1
    row = deps.logs.rows[0]
    assert row["provider"] == "claude"
    assert row["account_id"] == account.id
    assert row["model"] == "claude-opus-4-8"
    assert row["input_tokens"] == 3
    assert row["output_tokens"] == 5
    assert row["request_id"] == "r-stream"

    # Rate-limit cache written exactly once at the end.
    assert len(deps.repo.rate_limit_cache_writes) == 1


async def test_streaming_mid_stream_401_rotates_and_continues(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    """Mid-stream 401 triggers one rotate-then-retry; final events flush.

    The proxy service must NOT write two log rows; the retry stream's
    ``message_delta`` is the canonical usage source.
    """
    account = _make_account()
    deps.lb.choose = lambda **_: account
    deps.auth.rotate_return = _FakeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)

    # First stream: 1 header + 2 sse, then mid-stream 401.
    first_chunks = [
        StreamChunk(kind="headers", data={"anthropic-ratelimit-status": "allowed"}),
        StreamChunk(kind="sse", data=b"event: message_start\r\ndata: {}\r\n\r\n"),
        StreamChunk(kind="sse", data=b"event: content_block_delta\r\ndata: {}\r\n\r\n"),
        # Sentinel — the stub raises on this kind during iteration.
        StreamChunk(kind="raise", data=ClaudeAuthError("anthropic 401 mid-stream")),  # ty:ignore[invalid-argument-type]
    ]
    # Second stream: 1 header + 2 sse + 1 usage. Closes cleanly.
    second_chunks = [
        StreamChunk(kind="headers", data={"anthropic-ratelimit-status": "allowed"}),
        StreamChunk(
            kind="sse",
            data=b'event: message_delta\r\ndata: {"usage":{"input_tokens":7,"output_tokens":11}}\r\n\r\n',
        ),
        StreamChunk(kind="sse", data=b"event: message_stop\r\ndata: {}\r\n\r\n"),
        StreamChunk(kind="usage", data={"input_tokens": 7, "output_tokens": 11}),
    ]
    deps.chat.stream_messages_return = [first_chunks, second_chunks]

    received: list[StreamChunk] = []
    async for chunk in proxy_service.stream_messages(
        request_body={"model": "claude-opus-4-8", "stream": True},
        api_key=_make_api_key(provider_scope="claude"),
        request_id="r-mid-401",
    ):
        received.append(chunk)

    # One rotate after the first 401.
    assert len(deps.auth.rotate_calls) == 1
    # Two stream invocations recorded by the stub.
    assert len(deps.chat.stream_messages_call) == 2
    # Single log row from the second stream's usage.
    assert len(deps.logs.rows) == 1
    row = deps.logs.rows[0]
    assert row["input_tokens"] == 7
    assert row["output_tokens"] == 11


async def test_streaming_two_consecutive_401s_propagate(proxy_service: ClaudeProxyService, deps: _Deps) -> None:
    """Mid-stream 401, retry, mid-stream 401 again → ClaudeAuthError.

    The account is marked unhealthy via the load balancer helper before the
    exception propagates to the caller.
    """
    account = _make_account()
    deps.lb.choose = lambda **_: account
    deps.auth.rotate_return = _FakeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)

    # Both streams raise mid-stream 401 after yielding a couple of events.
    chunks_with_401 = [
        StreamChunk(kind="headers", data={"anthropic-ratelimit-status": "allowed"}),
        StreamChunk(kind="sse", data=b"event: message_start\r\ndata: {}\r\n\r\n"),
        StreamChunk(kind="raise", data=ClaudeAuthError("anthropic 401")),  # ty:ignore[invalid-argument-type]
    ]
    deps.chat.stream_messages_return = [chunks_with_401, chunks_with_401]

    with pytest.raises(ClaudeAuthError):
        async for chunk in proxy_service.stream_messages(
            request_body={"model": "claude-opus-4-8", "stream": True},
            api_key=_make_api_key(provider_scope="claude"),
            request_id="r-double-401-stream",
        ):
            pass

    # Two stream attempts; one rotate; account marked unhealthy.
    assert len(deps.chat.stream_messages_call) == 2
    assert len(deps.auth.rotate_calls) == 1
    assert any(call["account_id"] == account.id for call in deps.lb.record_health_calls)
    # No log row written on the error path.
    assert deps.logs.rows == []
