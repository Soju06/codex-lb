"""Redact Live WebSocket server scopes and rewrite duplicated Codex paths.

Some OpenAI-compatible clients unconditionally append ``/v1/`` to a configured
``/backend-api/codex`` base URL. For non-Live requests this middleware collapses
``/backend-api/codex/v1/<rest>`` to the canonical
``/backend-api/codex/<rest>`` route for both HTTP and WebSocket scopes.

Live WebSocket handshakes require the inverse ownership rule: Uvicorn retains
the original ASGI scope for accepted and rejected handshake logs, while the
application still needs the original path and query for routing. The middleware
therefore copies the original routing scope for downstream use, then redacts the
server-owned scope before any downstream accept or rejection can be emitted.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

from app.core.clients.proxy_websocket import REALTIME_LIVE_CALL_ID_ROUTE_REGEX

# Alias rewriting stays scoped to the duplicated Codex prefix. Live WebSocket
# redaction separately covers every routed ingress family plus the unsupported
# duplicated Live alias.
_CODEX_V1_PREFIX = "/backend-api/codex/v1/"
_CODEX_V1_PREFIX_BYTES = _CODEX_V1_PREFIX.encode("ascii")
_CODEX_CANONICAL_PREFIX = "/backend-api/codex/"
_CODEX_CANONICAL_PREFIX_BYTES = _CODEX_CANONICAL_PREFIX.encode("ascii")
_V1_LIVE_PREFIX = "/v1/live/"
_V1_LIVE_PREFIX_BYTES = _V1_LIVE_PREFIX.encode("ascii")
_V1_REALTIME_PATH = "/v1/realtime"
_V1_REALTIME_RAW_PATH = _V1_REALTIME_PATH.encode("ascii")
_REDACTED_CALL_ID = "<redacted>"
_REDACTED_CALL_ID_BYTES = b"%3Credacted%3E"
_REALTIME_LIVE_CALL_ID_PATTERN = re.compile(rf"{REALTIME_LIVE_CALL_ID_ROUTE_REGEX}\Z")
_REALTIME_LIVE_PATH_REDACTIONS = (
    (
        _CODEX_V1_PREFIX,
        f"{_CODEX_V1_PREFIX}{_REDACTED_CALL_ID}",
        _CODEX_V1_PREFIX_BYTES + _REDACTED_CALL_ID_BYTES,
    ),
    (
        _CODEX_CANONICAL_PREFIX,
        f"{_CODEX_CANONICAL_PREFIX}{_REDACTED_CALL_ID}",
        _CODEX_CANONICAL_PREFIX_BYTES + _REDACTED_CALL_ID_BYTES,
    ),
)


def _canonicalize_backend_api_codex_path(path: str) -> str:
    """Collapse ``/backend-api/codex/v1/<rest>`` -> ``/backend-api/codex/<rest>``.

    Returns the input unchanged for any path that is not the duplicated
    Codex ``/v1/`` shape. In particular, ``/backend-api/codex`` (no
    rest) and ``/backend-api/codex/v1`` (no further rest) are left
    alone -- those are legal request paths a future contributor might
    register, and collapsing them would silently change routing
    semantics.
    """
    if not path.startswith(_CODEX_V1_PREFIX):
        return path
    return _CODEX_CANONICAL_PREFIX + path[len(_CODEX_V1_PREFIX) :]


def _realtime_live_scope_redaction(path: str) -> tuple[str, bytes] | None:
    if path == _V1_REALTIME_PATH:
        return _V1_REALTIME_PATH, _V1_REALTIME_RAW_PATH

    # The v3 ingress owns this whole namespace, including requests whose
    # suffix cannot match a route. Rejected handshakes must therefore redact
    # malformed, empty, and overlong suffixes before Uvicorn can log them.
    if path.startswith(_V1_LIVE_PREFIX):
        return (
            f"{_V1_LIVE_PREFIX}{_REDACTED_CALL_ID}",
            _V1_LIVE_PREFIX_BYTES + _REDACTED_CALL_ID_BYTES,
        )

    # Generic Codex paths share their namespace with non-Live routes, so their
    # redaction boundary remains the exact Live call-id grammar.
    for prefix, redacted_path, redacted_raw_path in _REALTIME_LIVE_PATH_REDACTIONS:
        if path.startswith(prefix) and _REALTIME_LIVE_CALL_ID_PATTERN.fullmatch(path[len(prefix) :]) is not None:
            return redacted_path, redacted_raw_path
    return None


def redact_realtime_live_path(path: str) -> str:
    """Return the credential-safe path used by pre-routing diagnostics."""

    redaction = _realtime_live_scope_redaction(path)
    return path if redaction is None else redaction[0]


def _canonicalize_raw_path(raw_path: bytes) -> bytes:
    if not raw_path.startswith(_CODEX_V1_PREFIX_BYTES):
        return raw_path
    return _CODEX_CANONICAL_PREFIX_BYTES + raw_path[len(_CODEX_V1_PREFIX_BYTES) :]


class BackendApiCodexV1AliasMiddleware:
    """Keep Live handshake logs private and canonicalise the duplicated Codex prefix.

    This runs immediately inside trusted-proxy projection. For a Live WebSocket,
    the downstream application receives a copy containing the projected client
    metadata and original routing values while the server-owned scope is
    redacted in place. Lifespan and non-Live scopes preserve the existing
    alias-only behavior.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        path = scope.get("path")

        if scope_type == "websocket" and isinstance(path, str):
            redaction = _realtime_live_scope_redaction(path)
            if redaction is not None:
                routing_scope = dict(scope)
                routing_scope["path"] = _canonicalize_backend_api_codex_path(path)
                if isinstance(raw_path := routing_scope.get("raw_path"), bytes):
                    routing_scope["raw_path"] = _canonicalize_raw_path(raw_path)
                redacted_path, redacted_raw_path = redaction
                scope["path"] = redacted_path
                scope["raw_path"] = redacted_raw_path
                scope["query_string"] = b""
                await self.app(routing_scope, receive, send)
                return

        if scope_type in {"http", "websocket"} and isinstance(path, str) and path.startswith(_CODEX_V1_PREFIX):
            rewritten = _canonicalize_backend_api_codex_path(path)
            if rewritten != path:
                # Preserve the server-owned scope when only downstream routing changes.
                routing_scope = dict(scope)
                routing_scope["path"] = rewritten
                if isinstance(raw_path := routing_scope.get("raw_path"), bytes):
                    routing_scope["raw_path"] = _canonicalize_raw_path(raw_path)
                scope = routing_scope
        await self.app(scope, receive, send)


def add_backend_api_codex_v1_alias_middleware(app: FastAPI) -> None:
    """Register path rewriting and Live scope redaction inside trusted proxying."""

    app.add_middleware(BackendApiCodexV1AliasMiddleware)
