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
so the canonical handler picks the request up unchanged. See
``openspec/changes/strip-codex-v1-prefix/`` for the spec delta.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import Response

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


def add_backend_api_codex_v1_alias_middleware(app: FastAPI) -> None:
    """Register a path-rewrite middleware for the duplicated prefix.

    Implemented as a standard FastAPI HTTP middleware so it runs before
    Starlette's router matches a route. Both ``scope["path"]`` and
    ``scope["raw_path"]`` are kept in sync so downstream middleware that
    re-derive the request URL from either field see the canonical form.
    """

    @app.middleware("http")
    async def alias_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        scope = request.scope
        path = scope.get("path")
        if isinstance(path, str) and path.startswith(_CODEX_V1_PREFIX):
            rewritten = _canonicalize_backend_api_codex_path(path)
            if rewritten != path:
                scope["path"] = rewritten
                raw_path = scope.get("raw_path")
                if isinstance(raw_path, bytes):
                    scope["raw_path"] = _canonicalize_raw_path(raw_path)
        return await call_next(request)
