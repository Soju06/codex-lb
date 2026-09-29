"""Serve Codex's ChatGPT-backend calls when ``chatgpt_base_url`` is codex-lb.

See ``openspec/specs/codex-backend-passthrough``. With ``chatgpt_base_url``
set to codex-lb's ``/backend-api``, Codex sends every backend call on its
chatgpt.com path. codex-lb answers pooled usage itself (the ``/wham`` aliases
below) and forwards the rest upstream unchanged under the caller's identity.
This router is included after every other ``/backend-api`` router, so
first-match routing keeps served routes authoritative.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import unquote

import aiohttp
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.routing import Match
from starlette.types import Receive, Scope, Send

from app.core.auth.dependencies import (
    CodexCallerIdentity,
    set_openai_error_format,
    validate_codex_backend_passthrough_identity,
)
from app.core.clients.codex import CodexTransportError
from app.core.clients.codex_backend import CodexBackendStream, open_codex_backend_stream
from app.core.clients.proxy import CODEX_LB_REQUIRED_CAPABILITY_HEADER
from app.core.clock import clock_for
from app.core.errors import openai_error
from app.modules.proxy import api as proxy_api
from app.modules.proxy.schemas import ConsumeRateLimitResetCreditResponse, RateLimitStatusPayload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"], dependencies=[Depends(set_openai_error_format)])

_PASSTHROUGH_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]

# Allowlist, not denylist: anything upstream adds (cookies, edge headers,
# encodings the body no longer has) stays behind. ``www-authenticate`` carries
# MCP auth discovery; ``mcp-session-id`` binds later MCP calls to this session.
_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "mcp-session-id",
        "openai-processing-ms",
        "request-id",
        "retry-after",
        "www-authenticate",
        "x-request-id",
    }
)

_FORBIDDEN_PATH_CHARACTERS = frozenset("?#\\")

# Pool-routed namespaces: an unserved path here must not bypass the pool under
# the caller's own account (a new model endpoint, or a pool-pinned file id).
_CLOSED_NAMESPACES = ("codex", "files", "transcribe")


class _UpstreamStreamingResponse(StreamingResponse):
    """Close the upstream response however the downstream side ends.

    The body generator releases upstream when it finishes or is cancelled, but
    it never runs at all if the response fails before the first chunk (or for
    HEAD), so the release is also owned here.
    """

    def __init__(self, stream: CodexBackendStream, *, method: str, path: str, account_id: str) -> None:
        super().__init__(
            stream.iter_body(),
            status_code=stream.status_code,
            headers={key: value for key, value in stream.headers.items() if key.lower() in _RESPONSE_HEADERS},
        )
        self._upstream = stream
        self._method = method
        self._path = path
        self._account_id = account_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        outcome: str | None = None
        error: str | None = None
        try:
            await super().__call__(scope, receive, send)
        except aiohttp.ClientError as exc:
            # Checked first: aiohttp.ClientOSError / ClientConnectionResetError
            # subclass OSError but mean the *upstream* failed, not the caller.
            outcome, error = "error", type(exc).__name__
            raise
        except (asyncio.CancelledError, ClientDisconnect, OSError) as exc:
            # Under ASGI 2.4 Starlette re-raises any OSError from the body
            # iterator as ClientDisconnect; the upstream error is its context.
            upstream_error = exc.__context__ if isinstance(exc, ClientDisconnect) else None
            if isinstance(upstream_error, aiohttp.ClientError):
                outcome, error = "error", type(upstream_error).__name__
            else:
                outcome = "client_disconnect"
            raise
        except Exception as exc:
            outcome, error = "error", type(exc).__name__
            raise
        finally:
            await self._upstream.aclose()
            if outcome is None:
                # Under ASGI < 2.4 Starlette ends a stream on disconnect without
                # raising here; an unfinished body is the tell.
                finished = self._upstream.body_completed or self._method == "HEAD"
                outcome = "completed" if finished else "client_disconnect"
            self._log_end(outcome, error)

    def _log_end(self, outcome: str, error: str | None) -> None:
        stream = self._upstream
        level = logging.WARNING if outcome == "error" or stream.status_code >= 500 else logging.INFO
        logger.log(
            level,
            "Codex backend passthrough method=%s path=%s account_id=%s status=%s outcome=%s duration_ms=%d bytes=%d%s",
            self._method,
            self._path,
            self._account_id,
            stream.status_code,
            outcome,
            (clock_for(stream).monotonic() - stream.opened_at) * 1000,
            stream.bytes_relayed,
            f" error={error}" if error else "",
        )


def _decline_reason(rest: str, headers: Headers) -> str | None:
    """Why the passthrough must not forward this request, or ``None`` if it may."""

    segments = rest.split("/")
    if (
        not rest
        or rest.startswith("/")
        or any(segment in {".", ".."} for segment in segments)
        or any(char in _FORBIDDEN_PATH_CHARACTERS or ord(char) < 0x20 for char in rest)
    ):
        return "unsafe_path"
    if segments[0] in _CLOSED_NAMESPACES:
        return "closed_namespace"
    if headers.getlist(CODEX_LB_REQUIRED_CAPABILITY_HEADER):
        return "capability_header"
    scheme, _, token = (headers.get("authorization") or "").strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return "no_bearer"
    if token.startswith("sk-clb-"):
        return "api_key_principal"
    return None


class _ForwardableBackendRoute(APIRoute):
    """Match only requests the passthrough may forward.

    Everything else falls through to codex-lb's usual unmatched-path handling,
    so unknown, pool-namespace, anonymous, and codex-lb-principal requests keep
    the exact 404/405 envelopes they had before this route existed.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if match is Match.NONE:
            return match, child_scope
        reason = _decline_reason(child_scope["path_params"]["rest"], Headers(scope=scope))
        if reason is None and self._served_locally(scope):
            reason = "served_locally"
        if reason is not None:
            logger.debug(
                "Codex backend passthrough declined method=%s path=%s reason=%s",
                scope.get("method"),
                scope.get("path"),
                reason,
            )
            return Match.NONE, {}
        return match, child_scope

    def _served_locally(self, scope: Scope) -> bool:
        """Whether another ``/backend-api`` route owns this path under any method.

        Starlette prefers a later full match over an earlier partial one, so
        without this a wrong-method call to a local path would be forwarded
        instead of answered 405.
        """

        router = getattr(scope.get("app"), "router", None)
        for route in getattr(router, "routes", ()):
            if route is self or not getattr(route, "path", "").startswith("/backend-api/"):
                continue
            if route.matches(scope)[0] is not Match.NONE:
                return True
        return False


# Codex calls these on the ``/wham`` path in the ``/backend-api`` style; they
# reuse the ``/api/codex`` handlers so auth and payload cannot drift apart.
for _path in ("/backend-api/wham/usage", "/backend-api/wham/usage/"):
    router.add_api_route(
        _path,
        proxy_api.codex_usage,
        methods=["GET"],
        response_model=RateLimitStatusPayload,
        include_in_schema=_path.endswith("usage"),
    )
for _path in (
    "/backend-api/wham/rate-limit-reset-credits/consume",
    "/backend-api/wham/rate-limit-reset-credits/consume/",
):
    router.add_api_route(
        _path,
        proxy_api.codex_consume_rate_limit_reset_credit,
        methods=["POST"],
        response_model=ConsumeRateLimitResetCreditResponse,
        include_in_schema=_path.endswith("consume"),
    )


_BACKEND_PREFIX = "/backend-api/"


def _strip_raw_mount_prefix(raw: str, root_path: str) -> str:
    """Drop the mount prefix from a still-encoded path, or return it unchanged.

    ``root_path`` is decoded, while ``raw`` is not. uvicorn prepends the literal
    ``--root-path`` to ``raw_path``; a Starlette ``Mount`` leaves the client's
    encoded prefix in place; ``httpx.ASGITransport`` omits the prefix entirely.
    The prefix is matched at a segment boundary, literally first and then by
    what it decodes to, so ``/a%20b`` matches a ``root_path`` of ``/a b``.
    """

    if not root_path:
        return raw
    if raw.startswith(root_path):
        return raw[len(root_path) :]
    end = 0
    while (end := raw.find("/", end + 1)) != -1:
        if unquote(raw[:end]) == root_path:
            return raw[end:]
    if unquote(raw) == root_path:
        return ""
    return raw


def _raw_upstream_rest(scope: Scope, rest: str) -> str | None:
    """The still-percent-encoded path after ``/backend-api/``, or ``rest`` without a raw path.

    Starlette decodes ``rest``, so ``settings%2Fdetail`` would otherwise reach
    upstream as ``settings/detail``. Returns ``None`` when the raw path does not
    decode back to ``rest`` (the route matched something other than what would
    be forwarded); the caller must refuse rather than guess.
    """

    raw_path = scope.get("raw_path")
    if not raw_path:
        return rest
    raw = raw_path.decode("latin-1").split("?", 1)[0]
    raw = _strip_raw_mount_prefix(raw, scope.get("root_path") or "")
    if not raw.startswith(_BACKEND_PREFIX):
        return None
    raw_rest = raw[len(_BACKEND_PREFIX) :]
    # Only the decoded form is ever checked by ``_decline_reason``; a raw path
    # that decodes differently would forward bytes that check never saw.
    if unquote(raw_rest) != rest:
        return None
    return raw_rest


async def codex_backend_passthrough(
    request: Request,
    rest: str,
    identity: CodexCallerIdentity = Depends(validate_codex_backend_passthrough_identity),
) -> Response:
    upstream_rest = _raw_upstream_rest(request.scope, rest)
    if upstream_rest is None:
        return JSONResponse(
            status_code=400,
            content=openai_error("invalid_request_error", "Request path is not valid"),
        )
    body = await request.body()
    try:
        stream = await open_codex_backend_stream(
            upstream_rest,
            method=request.method,
            body=body or None,
            raw_query=request.scope["query_string"].decode("latin-1"),
            inbound_headers=request.headers,
            access_token=identity.access_token,
            chatgpt_account_id=identity.chatgpt_account_id,
            route=identity.route,
        )
    except (CodexTransportError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning(
            "Codex backend passthrough upstream unavailable method=%s path=%s account_id=%s error=%s",
            request.method,
            upstream_rest,
            identity.account_id,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=502,
            content=openai_error("upstream_unavailable", "Upstream ChatGPT backend is unavailable"),
        )
    logger.debug(
        "Codex backend passthrough opened method=%s path=%s account_id=%s status=%s",
        request.method,
        upstream_rest,
        identity.account_id,
        stream.status_code,
    )
    return _UpstreamStreamingResponse(stream, method=request.method, path=upstream_rest, account_id=identity.account_id)


router.add_api_route(
    "/backend-api/{rest:path}",
    codex_backend_passthrough,
    methods=_PASSTHROUGH_METHODS,
    include_in_schema=False,
    response_model=None,
    route_class_override=_ForwardableBackendRoute,
)
