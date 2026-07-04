"""Integration tests for the Claude API layer (Phase 10).

These tests cover:

- ``GET /claude/v1/models`` — public catalog.
- ``POST /claude/v1/messages`` — provider-scoped auth + streaming + non-streaming.
- ``/api/claude/accounts`` admin CRUD — including the hard-line token-leak
  invariant: no field matching ``*token*encrypted*`` may appear in the
  admin response payload.

The proxy routes are exercised by stubbing
``app.state.claude_proxy_service`` because the lifespan in tests is replaced
with a no-op (see :mod:`tests.conftest`). The admin routes hit the real
SQLAlchemy session.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from app.core.clients.anthropic.chat import StreamChunk

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Stubs for the proxy service used by the messages endpoints
# ---------------------------------------------------------------------------


class _StubAccount:
    """Minimal Account-shaped value stub for the proxy tests."""

    def __init__(self, account_id: str = "claude-1") -> None:
        self.id = account_id


class _StubProxyService:
    """Fake :class:`ClaudeProxyService` for routing smoke tests.

    Forwards non-streaming bodies verbatim + returns a fixed headers dict;
    streams yield two ``sse`` chunks then a usage chunk. The integration
    tests assert on the response shape, not on Phase 9 internals.
    """

    def __init__(self) -> None:
        self.non_stream_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def stream_or_complete_messages(
        self,
        *,
        request_body: dict[str, Any],
        api_key: Any,
        request_id: str,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        self.non_stream_calls.append({"body": request_body, "request_id": request_id})
        body = {"id": "msg_passed", "model": request_body.get("model")}
        headers = {
            "anthropic-ratelimit-requests-remaining": "42",
            "anthropic-ratelimit-status": "allowed",
            "content-type": "application/json",
            "x-unrelated": "drop-me",
        }
        return body, headers

    async def stream_messages(
        self,
        *,
        request_body: dict[str, Any],
        api_key: Any,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        self.stream_calls.append({"body": request_body, "request_id": request_id})

        async def _gen() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(
                kind="headers",
                data={"anthropic-ratelimit-requests-remaining": "11"},
            )
            yield StreamChunk(
                kind="sse",
                data=b'event: message_start\r\ndata: {"type":"message_start"}\r\n\r\n',
            )
            yield StreamChunk(
                kind="sse",
                data=b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n',
            )
            yield StreamChunk(
                kind="usage",
                data={"input_tokens": 1, "output_tokens": 2},
            )

        # Return the async generator object so ``await service.stream_messages(...)``
        # in the API layer resolves to something that supports ``async for``.
        return _gen()


@pytest.fixture()
def stubbed_claude_service(app_instance):
    """Install a ``ClaudeProxyService`` stub on the FastAPI app state.

    The lifespan in the test setup short-circuits, so the singleton is not
    populated automatically; this fixture replaces it with the stub used by
    both proxy tests and admin tests.
    """
    stub = _StubProxyService()
    stub.select_account = lambda *_a, **_kw: _StubAccount()  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    stub.record_error = lambda *_a, **_kw: None  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    stub.get_access_token = lambda _account: "AT"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    stub.rotate_claude_access_token = lambda *_a, **_kw: None  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    app_instance.state.claude_proxy_service = stub  # type: ignore[assignment]
    return stub


@pytest.fixture()
def override_claude_key(app_instance):
    """Patch the inner ``validate_proxy_api_key`` dependency so the
    ``api_key_validator_with_provider("claude")`` wrapper around it runs
    end-to-end with a pre-canned ``ApiKeyData``.

    Overriding the wrapper itself would skip the provider-scope check,
    which is what the rejection test asserts. We override the inner
    validator so FastAPI's dep tree (validator + scope check) stays intact.
    """
    from app.core.auth import dependencies as auth_deps

    state: dict[str, Any] = {"key": None}

    async def _fake(request: Any = None) -> Any:
        return state["key"]

    app_instance.dependency_overrides[auth_deps.validate_proxy_api_key] = _fake
    try:
        yield state
    finally:
        app_instance.dependency_overrides.pop(auth_deps.validate_proxy_api_key, None)


# ---------------------------------------------------------------------------
# /claude/v1/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_returns_static_catalog(async_client):
    response = await async_client.get("/claude/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    ids = sorted(item["id"] for item in payload["data"])
    # hardcoded catalog from app/modules/claude/models_catalog.py
    assert ids == ["claude-haiku-4-5-20251001", "claude-opus-4-8", "claude-sonnet-4-6"]
    for item in payload["data"]:
        assert item["object"] == "model"
        assert "display_name" in item


# ---------------------------------------------------------------------------
# /claude/v1/messages — non-streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_messages_non_streaming_passes_body_and_headers(
    async_client, stubbed_claude_service, override_claude_key
):
    from types import SimpleNamespace

    override_claude_key["key"] = SimpleNamespace(
        id="key-1",
        provider_scope="codex,claude",
    )

    body = {"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    response = await async_client.post("/claude/v1/messages", json=body)
    assert response.status_code == 200
    assert response.json() == {"id": "msg_passed", "model": "claude-opus-4-8"}
    # Upstream ``anthropic-ratelimit-*`` + ``content-type`` MUST be re-emitted;
    # the unrelated ``x-unrelated`` header MUST NOT leak into the response.
    assert response.headers["anthropic-ratelimit-status"] == "allowed"
    assert response.headers["anthropic-ratelimit-requests-remaining"] == "42"
    assert "x-unrelated" not in {k.lower() for k in response.headers.keys()}

    sent_body = stubbed_claude_service.non_stream_calls[0]["body"]
    # HTTPX serializes the request body through JSON, so we cannot preserve
    # identity through the wire. We assert on content equivalence here and
    # the Phase 9 unit test ``test_request_body_passed_verbatim_no_copy``
    # covers identity preservation at the service boundary.
    assert sent_body == body


# ---------------------------------------------------------------------------
# /claude/v1/messages — streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_messages_streaming_returns_text_event_stream(
    async_client, stubbed_claude_service, override_claude_key
):
    from types import SimpleNamespace

    override_claude_key["key"] = SimpleNamespace(id="key-1", provider_scope="claude")

    body = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    async with async_client.stream("POST", "/claude/v1/messages", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks: list[str] = []
        async for line in response.aiter_lines():
            if line:
                chunks.append(line)

    body_text = "\n".join(chunks)
    assert "event: message_start" in body_text
    assert "event: message_stop" in body_text
    # Internal ``usage``/``headers`` chunks MUST NOT be serialized as SSE.
    assert "input_tokens" not in body_text


@pytest.mark.asyncio
async def test_post_messages_streaming_releases_iterator_on_unexpected_exception(
    async_client, stubbed_claude_service, override_claude_key
):
    """Streaming path MUST release the upstream iterator when the chat
    client raises a non-typed exception (e.g. transport disconnect).

    Covers the spec requirement *Streaming proxy cleanup on unexpected
    exceptions*: the ``_gen`` wrapper's ``finally`` block MUST call
    ``aclose()`` on the underlying ``StreamChunk`` iterator exactly once
    even when the async-for loop is interrupted by a ``RuntimeError``
    (or any non-typed exception class). The iterator's ``aclose`` is
    observable via a wrapper that records invocations.

    The exception MUST propagate to the FastAPI request handler as the
    original ``RuntimeError`` (NOT a typed HTTP error envelope) so the
    proxy surfaces transport disconnects without pretending they were
    one of the documented Claude error classes.
    """
    from types import SimpleNamespace

    override_claude_key["key"] = SimpleNamespace(id="key-1", provider_scope="claude")

    aclose_calls: list[None] = []

    class _RecordingIterator:
        """Async iterator that raises ``RuntimeError`` after the first chunk
        and records ``aclose()`` invocations.
        """

        def __init__(self) -> None:
            self._yielded_header = False

        def __aiter__(self) -> "_RecordingIterator":
            return self

        async def __anext__(self):
            if not self._yielded_header:
                self._yielded_header = True
                return StreamChunk(
                    kind="headers",
                    data={"anthropic-ratelimit-status": "allowed"},
                )
            raise RuntimeError("simulated transport disconnect")

        async def aclose(self) -> None:
            aclose_calls.append(None)

    async def _exploding_stream(
        *,
        request_body: dict[str, Any],
        api_key: Any,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        return _RecordingIterator()

    stubbed_claude_service.stream_messages = _exploding_stream  # type: ignore[method-assign]

    body = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    # The route handler propagates the RuntimeError out of the StreamingResponse
    # body; httpx surfaces this as an exception from ``aiter_bytes``. We catch
    # the exception explicitly (NOT ``contextlib.suppress(Exception)``) so the
    # test fails on a non-typed exception that the proxy accidentally swallows.
    with pytest.raises(RuntimeError, match="simulated transport disconnect"):
        async with async_client.stream("POST", "/claude/v1/messages", json=body) as response:
            async for _ in response.aiter_bytes():
                pass

    # Exactly-once: the iterator's ``aclose`` MUST be invoked once and only
    # once, regardless of how the generator is torn down.
    assert aclose_calls == [None], f"expected aclose() to be invoked exactly once; got {len(aclose_calls)} call(s)"


# ---------------------------------------------------------------------------
# /api/claude/accounts admin CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_claude_accounts_returns_empty_when_no_rows(async_client, stubbed_claude_service):
    response = await async_client.get("/api/claude/accounts")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_post_claude_accounts_returns_201_and_does_not_leak_tokens(async_client, stubbed_claude_service):
    payload = {
        "claudeAccountUuid": "acc-uuid-post",
        "accessToken": "sk-ant-oat01-PLAINTEXT-AT",
        "refreshToken": "sk-ant-ort01-PLAINTEXT-RT",
        "expiresInSeconds": 3600,
        "userEmail": "owner@example.com",
    }
    response = await async_client.post("/api/claude/accounts", json=payload)
    assert response.status_code == 201
    body = response.json()
    # Hard-line token-leak invariant: any field whose name matches the
    # *token + encrypted* rule is forbidden in admin responses.
    for key in body.keys():
        lowered = key.lower()
        assert "token" not in lowered or "encrypted" not in lowered, (
            f"unexpected token field in admin response: {key!r}"
        )
        assert "PLAINTEXT" not in str(body[key])
    assert body["claudeAccountUuid"] == "acc-uuid-post"
    assert body["userEmail"] == "owner@example.com"
    assert body["isActive"] is True

    listed = await async_client.get("/api/claude/accounts")
    assert listed.status_code == 200
    listed_bodies = listed.json()
    assert len(listed_bodies) == 1
    listed_account = listed_bodies[0]
    for key in listed_account.keys():
        lowered = key.lower()
        assert "token" not in lowered or "encrypted" not in lowered, (
            f"unexpected token field in admin list response: {key!r}"
        )
    # And the raw token MUST NOT be present in any string-typed value.
    raw = json.dumps(listed_account)
    assert "PLAINTEXT-AT" not in raw
    assert "PLAINTEXT-RT" not in raw


@pytest.mark.asyncio
async def test_patch_disable_then_enable_toggles_status(async_client, stubbed_claude_service):
    add = await async_client.post(
        "/api/claude/accounts",
        json={
            "claudeAccountUuid": "acc-toggle",
            "accessToken": "AT",
            "refreshToken": "RT",
            "expiresInSeconds": 3600,
        },
    )
    assert add.status_code == 201
    account_id = add.json()["id"]

    disable = await async_client.patch(
        f"/api/claude/accounts/{account_id}/disable",
        json={"reason": "operator test"},
    )
    assert disable.status_code == 204

    after_disable = await async_client.get("/api/claude/accounts")
    [row] = after_disable.json()
    assert row["id"] == account_id
    assert row["isActive"] is False
    # status field flows through the camel serializer
    assert row["status"] in {"deactivated"}

    enable = await async_client.patch(f"/api/claude/accounts/{account_id}/enable")
    assert enable.status_code == 204

    after_enable = await async_client.get("/api/claude/accounts")
    [row] = after_enable.json()
    assert row["isActive"] is True
    assert row["status"] == "active"


# ---------------------------------------------------------------------------
# Provider-scope auth: codex-only key hits the /claude/v1/messages route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_rejects_codex_only_key(async_client, stubbed_claude_service, override_claude_key):
    from types import SimpleNamespace

    override_claude_key["key"] = SimpleNamespace(
        id="key-codex-only",
        provider_scope="codex",
    )

    response = await async_client.post(
        "/claude/v1/messages",
        json={
            "model": "claude-opus-4-8",
            "messages": [],
            "stream": False,
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert "claude" in str(body).lower()
