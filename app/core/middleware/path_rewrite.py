"""Path-rewrite middleware for backwards-compatible `/v1/` URL handling.

Some OpenAI-compatible clients unconditionally append ``/v1/`` to
whatever base URL the operator configured. When the configured base URL
already terminates at ``/backend-api/codex`` (codex-lb's Codex-style
entry point), those clients end up hitting
``/backend-api/codex/v1/<rest>`` -- a shape codex-lb does not register
because the OpenAI-style endpoints are mounted at the top-level
``/v1/<rest>`` and the Codex-style endpoints at
``/backend-api/codex/<rest>``.

This middleware collapses the duplicated ``/v1`` segment in-place by
mutating ``scope["path"]`` (and ``scope["raw_path"]``) before routing,
so the canonical handler picks the request up unchanged. Implemented as
a pure ASGI middleware so the rewrite covers both HTTP and WebSocket
scopes -- ``app/modules/proxy/api.py`` exposes websocket endpoints under
``/backend-api/codex`` (and ``/v1``), so a client that appends ``/v1``
to its ``/backend-api/codex`` base URL needs the alias for handshakes
just as much as it does for HTTP requests. See
``openspec/changes/strip-codex-v1-prefix/`` for the spec delta.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

# The middleware is intentionally scoped to the duplicated Codex prefix
# only. The top-level ``/v1/`` namespace is the canonical OpenAI-style
# route surface and must be left alone.
_CODEX_V1_PREFIX = "/backend-api/codex/v1/"
_CODEX_V1_PREFIX_BYTES = _CODEX_V1_PREFIX.encode("ascii")
_CODEX_CANONICAL_PREFIX = "/backend-api/codex/"
_CODEX_CANONICAL_PREFIX_BYTES = _CODEX_CANONICAL_PREFIX.encode("ascii")


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


def _canonicalize_raw_path(raw_path: bytes) -> bytes:
    if not raw_path.startswith(_CODEX_V1_PREFIX_BYTES):
        return raw_path
    return _CODEX_CANONICAL_PREFIX_BYTES + raw_path[len(_CODEX_V1_PREFIX_BYTES) :]


class BackendApiCodexV1AliasMiddleware:
    """ASGI middleware that canonicalises the duplicated Codex prefix.

    Runs before Starlette's router matches a route. Both ``scope["path"]``
    and ``scope["raw_path"]`` are kept in sync so downstream middleware
    that re-derive the request URL from either field see the canonical
    form.

    Handles both ``http`` and ``websocket`` scopes -- HTTP-only middleware
    misses websocket handshakes, which is the bug Codex flagged on #610.
    Lifespan and other scope types pass through untouched.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path")
            if isinstance(path, str) and path.startswith(_CODEX_V1_PREFIX):
                rewritten = _canonicalize_backend_api_codex_path(path)
                if rewritten != path:
                    # Copy the scope so we don't mutate the caller's dict;
                    # ASGI servers may reuse scope instances across calls.
                    scope = dict(scope)
                    scope["path"] = rewritten
                    raw_path = scope.get("raw_path")
                    if isinstance(raw_path, bytes):
                        scope["raw_path"] = _canonicalize_raw_path(raw_path)
        await self.app(scope, receive, send)


def add_backend_api_codex_v1_alias_middleware(app: FastAPI) -> None:
    """Register the path-rewrite ASGI middleware for the duplicated prefix."""

    app.add_middleware(BackendApiCodexV1AliasMiddleware)
