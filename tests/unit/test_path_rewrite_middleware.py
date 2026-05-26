from __future__ import annotations

import pytest

from app.core.middleware.path_rewrite import (
    BackendApiCodexV1AliasMiddleware,
    _canonicalize_backend_api_codex_path,
    _canonicalize_raw_path,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Aliased prefix collapses.
        ("/backend-api/codex/v1/models", "/backend-api/codex/models"),
        ("/backend-api/codex/v1/responses", "/backend-api/codex/responses"),
        (
            "/backend-api/codex/v1/responses/compact",
            "/backend-api/codex/responses/compact",
        ),
        # Canonical paths are left alone.
        ("/backend-api/codex/models", "/backend-api/codex/models"),
        ("/backend-api/codex/responses", "/backend-api/codex/responses"),
        # No-rest sentinels MUST NOT be rewritten -- they are legal
        # paths a future contributor could register, and collapsing
        # them would silently change routing semantics.
        ("/backend-api/codex", "/backend-api/codex"),
        ("/backend-api/codex/v1", "/backend-api/codex/v1"),
        # Top-level /v1 is the canonical OpenAI-style namespace and is
        # explicitly out of scope.
        ("/v1/models", "/v1/models"),
        ("/v1/responses", "/v1/responses"),
        # Unrelated paths.
        ("/api/settings", "/api/settings"),
        ("/", "/"),
    ],
)
def test_canonicalize_backend_api_codex_path(raw: str, expected: str) -> None:
    assert _canonicalize_backend_api_codex_path(raw) == expected


def test_canonicalize_backend_api_codex_path_is_idempotent() -> None:
    once = _canonicalize_backend_api_codex_path("/backend-api/codex/v1/responses")
    twice = _canonicalize_backend_api_codex_path(once)
    assert once == twice == "/backend-api/codex/responses"


def test_canonicalize_raw_path_preserves_query_segment() -> None:
    # raw_path in ASGI includes only the path; query lives in
    # scope["query_string"]. The rewrite must therefore not split on
    # "?", but it should still byte-equal the canonical form.
    raw = b"/backend-api/codex/v1/models"
    assert _canonicalize_raw_path(raw) == b"/backend-api/codex/models"


def test_canonicalize_raw_path_noop_for_canonical() -> None:
    raw = b"/backend-api/codex/models"
    assert _canonicalize_raw_path(raw) is raw or _canonicalize_raw_path(raw) == raw


# ---- middleware scope-level tests --------------------------------------------
# Codex review on #610: the alias must rewrite websocket handshake scopes,
# not just HTTP scopes. The ASGI middleware below is invoked directly so we
# can assert exactly which `scope["path"]` reaches the downstream app.


class _RecordingApp:
    """Minimal downstream ASGI app that captures the scope it receives."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, scope: dict, receive, send) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(scope)


@pytest.mark.asyncio
async def test_middleware_rewrites_http_scope() -> None:
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    scope = {
        "type": "http",
        "path": "/backend-api/codex/v1/responses",
        "raw_path": b"/backend-api/codex/v1/responses",
    }

    async def _receive():
        return {"type": "http.request"}

    async def _send(message):
        pass

    await middleware(scope, _receive, _send)

    assert len(inner.calls) == 1
    seen = inner.calls[0]
    assert seen["path"] == "/backend-api/codex/responses"
    assert seen["raw_path"] == b"/backend-api/codex/responses"


@pytest.mark.asyncio
async def test_middleware_rewrites_websocket_scope() -> None:
    """The websocket handshake scope must be rewritten too.

    Without this the alias only covers HTTP and clients that append `/v1`
    to a `/backend-api/codex` base URL get a 404 on websocket handshakes
    while the equivalent HTTP request succeeds.
    """
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    scope = {
        "type": "websocket",
        "path": "/backend-api/codex/v1/responses",
        "raw_path": b"/backend-api/codex/v1/responses",
    }

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(scope, _receive, _send)

    assert len(inner.calls) == 1
    seen = inner.calls[0]
    assert seen["path"] == "/backend-api/codex/responses"
    assert seen["raw_path"] == b"/backend-api/codex/responses"


@pytest.mark.asyncio
async def test_middleware_leaves_lifespan_scope_untouched() -> None:
    """Lifespan and other non-http/non-websocket scopes must pass through
    unchanged so we never accidentally mutate ASGI server state."""
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    scope = {"type": "lifespan"}

    async def _receive():
        return {"type": "lifespan.startup"}

    async def _send(message):
        pass

    await middleware(scope, _receive, _send)

    assert len(inner.calls) == 1
    assert inner.calls[0] is scope


@pytest.mark.asyncio
async def test_middleware_does_not_mutate_caller_scope_on_rewrite() -> None:
    """ASGI servers can reuse scope dicts across calls. The rewrite must
    happen on a copy so the caller's dict survives unchanged."""
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    original_scope = {
        "type": "websocket",
        "path": "/backend-api/codex/v1/responses",
        "raw_path": b"/backend-api/codex/v1/responses",
    }
    snapshot = dict(original_scope)

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(original_scope, _receive, _send)

    assert original_scope == snapshot
    assert inner.calls[0]["path"] == "/backend-api/codex/responses"
