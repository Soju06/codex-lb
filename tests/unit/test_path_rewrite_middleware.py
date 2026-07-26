from __future__ import annotations

import pytest

from app.core.middleware.path_rewrite import (
    BackendApiCodexV1AliasMiddleware,
    _canonicalize_backend_api_codex_path,
    _canonicalize_raw_path,
)
from app.core.middleware.trusted_proxy_headers import TrustedProxyHeadersMiddleware

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


@pytest.mark.parametrize(
    ("path", "query_string", "redacted_path", "redacted_raw_path"),
    [
        (
            "/backend-api/codex/rtc_unit_current",
            b"intent=current-secret",
            "/backend-api/codex/<redacted>",
            b"/backend-api/codex/%3Credacted%3E",
        ),
        (
            "/v1/live/rtc_unit_v3",
            b"intent=v3-secret",
            "/v1/live/<redacted>",
            b"/v1/live/%3Credacted%3E",
        ),
        (
            "/v1/live/",
            b"intent=empty-suffix-secret",
            "/v1/live/<redacted>",
            b"/v1/live/%3Credacted%3E",
        ),
        (
            "/v1/live/not/a-valid-call-id",
            b"intent=malformed-secret",
            "/v1/live/<redacted>",
            b"/v1/live/%3Credacted%3E",
        ),
        (
            f"/v1/live/rtc_{'x' * 253}",
            b"intent=overlong-secret",
            "/v1/live/<redacted>",
            b"/v1/live/%3Credacted%3E",
        ),
        (
            "/v1/realtime",
            b"call_id=rtc_unit_legacy&intent=legacy-secret",
            "/v1/realtime",
            b"/v1/realtime",
        ),
    ],
    ids=[
        "current-app",
        "v3",
        "v3-empty-suffix",
        "v3-malformed-suffix",
        "v3-overlong-suffix",
        "legacy",
    ],
)
@pytest.mark.asyncio
async def test_middleware_redacts_server_scope_while_routing_with_original_live_values(
    path: str,
    query_string: bytes,
    redacted_path: str,
    redacted_raw_path: bytes,
) -> None:
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)
    server_scope = {
        "type": "websocket",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "headers": [(b"authorization", b"Bearer live-key")],
    }
    routing_snapshot = dict(server_scope)

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(server_scope, _receive, _send)

    assert inner.calls == [routing_snapshot]
    assert inner.calls[0] is not server_scope
    assert server_scope == {
        **routing_snapshot,
        "path": redacted_path,
        "raw_path": redacted_raw_path,
        "query_string": b"",
    }


@pytest.mark.asyncio
async def test_middleware_routes_duplicated_live_alias_canonically_while_redacting_server_scope() -> None:
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)
    server_scope = {
        "type": "websocket",
        "path": "/backend-api/codex/v1/rtc_unit_alias",
        "raw_path": b"/backend-api/codex/v1/rtc_unit_alias",
        "query_string": b"intent=alias-secret",
        "headers": [(b"authorization", b"Bearer live-key")],
    }

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(server_scope, _receive, _send)

    assert inner.calls == [
        {
            **server_scope,
            "path": "/backend-api/codex/rtc_unit_alias",
            "raw_path": b"/backend-api/codex/rtc_unit_alias",
            "query_string": b"intent=alias-secret",
        }
    ]
    assert server_scope == {
        "type": "websocket",
        "path": "/backend-api/codex/v1/<redacted>",
        "raw_path": b"/backend-api/codex/v1/%3Credacted%3E",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer live-key")],
    }


@pytest.mark.parametrize(
    ("path", "routed_path"),
    [
        ("/v1/responses", "/v1/responses"),
        ("/backend-api/codex/responses", "/backend-api/codex/responses"),
        ("/backend-api/codex/v1/responses", "/backend-api/codex/responses"),
        ("/backend-api/codex/rtc_", "/backend-api/codex/rtc_"),
        ("/backend-api/codex/v1/rtc_", "/backend-api/codex/rtc_"),
    ],
    ids=[
        "v1",
        "current-app",
        "duplicated-non-live-alias",
        "malformed-current-app-call-id",
        "malformed-duplicated-alias-call-id",
    ],
)
@pytest.mark.asyncio
async def test_middleware_leaves_non_live_server_scope_unchanged(path: str, routed_path: str) -> None:
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)
    server_scope = {
        "type": "websocket",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"call_id=ordinary&intent=visible",
    }
    snapshot = dict(server_scope)

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(server_scope, _receive, _send)

    assert server_scope == snapshot
    assert inner.calls[0]["path"] == routed_path
    assert inner.calls[0]["query_string"] == snapshot["query_string"]


@pytest.mark.asyncio
async def test_trusted_proxy_projection_precedes_live_scope_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    inner = _RecordingApp()
    middleware = TrustedProxyHeadersMiddleware(BackendApiCodexV1AliasMiddleware(inner))
    original_path = "/v1/live/not/a-valid-call-id"
    original_raw_path = b"/v1/live/not/a-valid-call-id"
    original_query = b"intent=private-secret"
    server_scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": original_path,
        "raw_path": original_raw_path,
        "query_string": original_query,
        "root_path": "",
        "headers": [
            (b"x-forwarded-for", b"203.0.113.41"),
            (b"x-forwarded-proto", b"https"),
        ],
        "client": ("127.0.0.1", 43120),
        "server": ("testserver", 80),
        "state": {},
    }

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(server_scope, _receive, _send)

    assert len(inner.calls) == 1
    routed_scope = inner.calls[0]
    assert routed_scope is not server_scope
    assert routed_scope["client"] == server_scope["client"] == ("203.0.113.41", 0)
    assert routed_scope["scheme"] == server_scope["scheme"] == "wss"
    assert (
        routed_scope["path"],
        routed_scope["raw_path"],
        routed_scope["query_string"],
    ) == (original_path, original_raw_path, original_query)
    assert (
        server_scope["path"],
        server_scope["raw_path"],
        server_scope["query_string"],
    ) == ("/v1/live/<redacted>", b"/v1/live/%3Credacted%3E", b"")


def test_production_registers_trusted_proxy_outside_live_scope_redaction() -> None:
    from app.main import create_app

    middleware = create_app().user_middleware
    middleware_classes = [entry.cls for entry in middleware]
    assert middleware_classes[0] is TrustedProxyHeadersMiddleware
    assert middleware_classes.index(TrustedProxyHeadersMiddleware) < middleware_classes.index(
        BackendApiCodexV1AliasMiddleware
    )
