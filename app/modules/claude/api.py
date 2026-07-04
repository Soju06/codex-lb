"""FastAPI routes for the Claude OAuth pool.

Exposes three surfaces:

1. Public proxy routes (``/claude/v1/*``) — the Anthropic-compatible
   passthrough. ``GET /claude/v1/models`` is a public catalog (matches
   Anthropic's "models are listed without auth" convention and we want
   static, non-secret data anyway). ``POST /claude/v1/messages`` is gated
   behind :func:`api_key_validator_with_provider` so mis-scoped keys cannot
   leak pool membership.
2. Admin CRUD (``/api/claude/accounts``) — gated behind the standard
   ``validate_dashboard_session`` dependency used by the rest of the
   operator dashboard. Token columns are stripped at serialization time.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.clients.anthropic.errors import (
    ClaudeAuthError,
    ClaudeRateLimited,
    ClaudeUpstreamError,
)
from app.db.session import get_session
from app.modules.api_keys.provider_auth import api_key_validator_with_provider
from app.modules.claude.auth_manager import ClaudeAccountAlreadyExists, ClaudeAuthManager
from app.modules.claude.models_catalog import list_claude_models
from app.modules.claude.repository import SqlClaudeAccountRepository
from app.modules.claude.schemas import (
    AddClaudeAccountRequest,
    DisableClaudeAccountRequest,
)
from app.modules.claude.service import (
    NoClaudeAccounts,
    ProviderScopeMismatch,
)

logger = logging.getLogger(__name__)


router = APIRouter()


async def _safe_aclose_iterator(iterator: Any) -> None:
    """Best-effort ``aclose()`` for an async iterator returned by the chat client.

    The streaming proxy path MUST release the upstream aiohttp response and
    any connection-pool resources on every exception class, not just the
    typed error branches. This helper tolerates iterators that don't implement
    ``aclose`` (test stubs) and accepts both sync and async ``aclose`` methods
    — see :meth:`app.core.clients.anthropic.chat.ClaudeChatClient._safe_aclose`
    for the canonical aiohttp implementation.
    """
    aclose = getattr(iterator, "aclose", None)
    if not callable(aclose):
        return
    try:
        result = aclose()
    except Exception:  # pragma: no cover - defensive
        return
    if hasattr(result, "__await__"):
        try:
            await result
        except Exception:  # pragma: no cover - defensive
            return


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


# Public /claude/v1/* proxy routes — auth via the provider-scoped validator
# factory. The dependency returns an ApiKeyData (or None when
# api_key_auth_enabled is False and the request is local — see the wrapped
# validator in app/core/auth/dependencies.py).
_claude_key = api_key_validator_with_provider("claude")


def _get_service(request: Request) -> Any:
    """Return the Claude proxy service for the running app.

    Production wiring in :mod:`app.main` registers a real
    :class:`app.modules.claude.service.ClaudeProxyService`; integration
    tests substitute a structural stub. The duck-typed check below
    accepts any object that exposes the two proxy methods as callable
    attributes — broader than ``isinstance`` but strict enough to surface
    broken wiring with a 503 instead of a confusing ``AttributeError``
    at request time.
    """
    state = getattr(request.app, "state", None)
    service = getattr(state, "claude_proxy_service", None) if state is not None else None
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "claude_proxy_service_unavailable"},
        )
    for required in ("stream_or_complete_messages", "stream_messages"):
        method = getattr(service, required, None)
        if not callable(method):
            raise HTTPException(
                status_code=503,
                detail={"error": "claude_proxy_service_invalid"},
            )
    return service


# ---------------------------------------------------------------------------
# Admin authentication context
# ---------------------------------------------------------------------------


async def _claude_admin_context(
    session: AsyncSession = Depends(get_session),
) -> tuple[AsyncSession, SqlClaudeAccountRepository, ClaudeAuthManager]:
    """Construct the (session, repo, manager) triple for admin endpoints.

    Note: the admin manager is constructed without an ``oauth_client``, so
    admin endpoints that attempt to call ``rotate_claude_access_token``
    will raise ``ClaudeAuthError("no OAuth client configured")``. The
    admin surface intentionally does NOT bootstrap-rotate a stale access
    token — operators must re-add the account (POST /api/claude/accounts)
    or wait for the auth guardian's next pass.
    """
    repository = SqlClaudeAccountRepository(session)
    manager = ClaudeAuthManager(repo=repository)
    return session, repository, manager


# ---------------------------------------------------------------------------
# Public proxy routes
# ---------------------------------------------------------------------------


@router.get("/claude/v1/models")
async def claude_list_models() -> dict[str, Any]:
    """Public Claude model catalog (matches Anthropic's no-auth convention)."""
    return list_claude_models()


@router.post("/claude/v1/messages")
async def claude_post_messages(
    request: Request,
    api_key: Any = Depends(_claude_key),
) -> Response:
    """Anthropic ``POST /v1/messages`` passthrough.

    Routes to :meth:`ClaudeProxyService.stream_or_complete_messages` for
    non-streaming requests and :meth:`ClaudeProxyService.stream_messages` for
    streaming requests. The body is passed through verbatim — no
    translation between Codex and Claude.
    """
    body = await request.json()
    is_stream = bool(body.get("stream"))
    request_id = request.headers.get("x-request-id", "")

    service = _get_service(request)

    if is_stream:
        # ``service.stream_messages`` is ``async def`` and returns the inner
        # async generator produced by ``chat.stream_messages``; we await the
        # call and iterate the returned iterator from outside the lifespan.
        iterator = await service.stream_messages(
            request_body=body,
            api_key=api_key,
            request_id=request_id,
        )

        async def _gen() -> Any:
            try:
                async for chunk in iterator:
                    # ``ClaudeChatClient`` emits sse/usage/headers chunks;
                    # forward only the SSE bytes — usage + headers are
                    # internal bookkeeping. Header bytes are not re-emitted
                    # because SSE carries everything the client needs.
                    if chunk.kind == "sse":
                        yield chunk.data
            except ClaudeAuthError:
                # Surface upstream 401 as 502 so the dashboard can
                # distinguish a failing pool from a misconfigured key.
                logger.warning(
                    "claude.stream.upstream_auth_error",
                    extra={"request_id": request_id},
                    exc_info=True,
                )
                yield b'event: error\ndata: {"error":"claude_upstream_auth_error"}\n\n'
            except ClaudeRateLimited:
                logger.warning(
                    "claude.stream.upstream_rate_limited",
                    extra={"request_id": request_id},
                    exc_info=True,
                )
                yield b'event: error\ndata: {"error":"claude_rate_limited"}\n\n'
            finally:
                # Release the upstream aiohttp response and connection-pool
                # resources for EVERY exception class — not just the typed
                # error branches above. ``BaseException`` (cancelled-error,
                # transport disconnect, JSON parse error) must NOT leak the
                # iterator; the ``finally`` block guarantees ``aclose`` runs
                # whether the loop completes, breaks, or raises.
                await _safe_aclose_iterator(iterator)

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    try:
        out_body, out_headers = await service.stream_or_complete_messages(
            request_body=body,
            api_key=api_key,
            request_id=request_id,
        )
    except ProviderScopeMismatch:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": {"code": "provider_scope_mismatch", "message": "API key is not authorized for claude"}},
        )
    except NoClaudeAccounts:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"code": "no_claude_accounts", "message": "no Claude accounts available"}},
        )
    except ClaudeRateLimited as exc:
        # Propagate upstream 429 as 502 so the dashboard can differentiate
        # between client error (the client itself) and a transient upstream
        # rejection.
        headers = getattr(exc, "headers", {}) or {}
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": {"code": "claude_rate_limited", "message": "Anthropic rate-limited this account"}},
            headers={k: v for k, v in headers.items() if k.lower().startswith("anthropic-")},
        )
    except ClaudeAuthError as exc:
        logger.warning(
            "claude.messages.upstream_auth_error",
            extra={"request_id": request_id},
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": {"code": "claude_upstream_auth_error", "message": str(exc)}},
        )
    except ClaudeUpstreamError as exc:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": {"code": "claude_upstream_error", "message": str(exc)}},
        )

    # Strip upstream headers except ones we'd actually want the client to see;
    # the proxy is a passthrough so we keep anthropic-* headers plus
    # content-type when present.
    forwarded_headers: dict[str, str] = {}
    for key, value in out_headers.items():
        lower = key.lower()
        if lower.startswith("anthropic-") or lower == "content-type":
            forwarded_headers[key] = value

    return JSONResponse(content=out_body, headers=forwarded_headers)


# ---------------------------------------------------------------------------
# Admin CRUD routes
# ---------------------------------------------------------------------------


# Admin sub-router gated behind the dashboard session. We use a separate
# ``APIRouter`` instance so the auth dependency stays scoped to /api/* paths
# and doesn't leak onto the proxy routes above.
admin_router = APIRouter(
    prefix="/api/claude",
    tags=["dashboard-claude"],
    dependencies=[
        Depends(validate_dashboard_session),
        Depends(set_dashboard_error_format),
    ],
)


# Columns whose names contain the substring ``token`` and ``encrypted`` MUST
# be stripped from the response. This is the project's hard-line token-leak
# invariant and is enforced at serialization time — never rely on the schema
# alone.
_TOKEN_FIELD_DENYLIST_SUBSTRINGS = ("token",)


def _is_token_field(name: str) -> bool:
    lowered = name.lower()
    return "token" in lowered and "encrypted" in lowered


def _serialize_account(account: Any) -> dict[str, Any]:
    """Project an ``Account`` row to the admin-facing payload.

    Plaintext tokens SHALL NOT be serialized. Any column whose name matches
    the token-denylist rule is dropped, along with the legacy Codex-only
    fields that are not relevant to a Claude account row. The ``status``
    enum alone conveys active/deactivated state (``active`` vs
    ``deactivated`` etc.) so we synthesize an ``isActive`` boolean for the
    dashboard.
    """
    status_value = getattr(account, "status", None)
    status_string = (
        status_value.value
        if hasattr(status_value, "value")
        else (str(status_value) if status_value is not None else None)
    )
    public = {
        "id": getattr(account, "id", None),
        "claudeAccountUuid": getattr(account, "claude_account_uuid", None),
        "userEmail": getattr(account, "claude_user_email", None),
        "userOrganizationUuid": getattr(account, "claude_user_organization_uuid", None),
        "status": status_string,
        "isActive": status_string == "active",
        "claudeAccessTokenExpiresAt": _iso(getattr(account, "claude_access_token_expires_at", None)),
        "lastUsedAt": _iso(getattr(account, "last_used_at", None)),
        "rateLimitRequestsRemaining": getattr(account, "rate_limit_requests_remaining", None),
        "rateLimitRequestsResetAt": _iso(getattr(account, "rate_limit_requests_reset_at", None)),
        "rateLimitInputTokensRemaining": getattr(account, "rate_limit_input_tokens_remaining", None),
        "rateLimitInputTokensResetAt": _iso(getattr(account, "rate_limit_input_tokens_reset_at", None)),
        "rateLimitOutputTokensRemaining": getattr(account, "rate_limit_output_tokens_remaining", None),
        "rateLimitOutputTokensResetAt": _iso(getattr(account, "rate_limit_output_tokens_reset_at", None)),
        "rateLimitStatus": getattr(account, "rate_limit_status", None),
        "deactivationReason": getattr(account, "deactivation_reason", None),
        "createdAt": _iso(getattr(account, "created_at", None)),
    }
    # Defensive: drop any column whose name triggers the token-denylist rule
    # even if the schema above accidentally omitted it. This catches future
    # additions like ``claude_refresh_token_hash`` etc.
    safe: dict[str, Any] = {}
    for key, value in public.items():
        if _is_token_field(key):
            continue
        safe[key] = value
    return safe


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


@admin_router.get("/accounts", response_model=None)
async def list_claude_accounts(
    context: tuple[AsyncSession, SqlClaudeAccountRepository, ClaudeAuthManager] = Depends(_claude_admin_context),
) -> list[dict[str, Any]]:
    _, repository, _ = context
    rows = await repository.list_accounts()
    return [_serialize_account(row) for row in rows]


@admin_router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=None)
async def add_claude_account(
    payload: AddClaudeAccountRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: tuple[AsyncSession, SqlClaudeAccountRepository, ClaudeAuthManager] = Depends(_claude_admin_context),
) -> dict[str, Any]:
    session, repository, manager = context
    try:
        new_id = await manager.add_claude_account(
            claude_account_uuid=payload.claude_account_uuid,
            access_token=payload.access_token,
            refresh_token=payload.refresh_token,
            expires_in_seconds=payload.expires_in_seconds,
            scopes=payload.scopes,
            user_email=payload.user_email,
            user_organization_uuid=payload.user_organization_uuid,
        )
    except ClaudeAccountAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "claude_account_already_exists", "message": str(exc)},
        ) from exc

    await session.commit()
    row = await repository.get_by_id(new_id)
    if row is None:  # pragma: no cover - should not happen
        raise HTTPException(status_code=500, detail="inserted account not found")
    return _serialize_account(row)


@admin_router.patch(
    "/accounts/{account_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disable_claude_account(
    account_id: str,
    payload: DisableClaudeAccountRequest | None = None,
    _write_access=Depends(require_dashboard_write_access),
    context: tuple[AsyncSession, SqlClaudeAccountRepository, ClaudeAuthManager] = Depends(_claude_admin_context),
) -> Response:
    session, repository, manager = context
    account = await repository.get_by_id(account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "claude_account_not_found", "message": f"unknown Claude account '{account_id}'"},
        )
    reason = payload.reason if payload else None
    disabled = await manager.disable_claude_account(account, reason=reason)
    if not disabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "claude_account_not_found", "message": f"unknown Claude account '{account_id}'"},
        )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.patch(
    "/accounts/{account_id}/enable",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def enable_claude_account(
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: tuple[AsyncSession, SqlClaudeAccountRepository, ClaudeAuthManager] = Depends(_claude_admin_context),
) -> Response:
    session, repository, manager = context
    account = await repository.get_by_id(account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "claude_account_not_found", "message": f"unknown Claude account '{account_id}'"},
        )
    enabled = await manager.enable_claude_account(account)
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "claude_account_not_found", "message": f"unknown Claude account '{account_id}'"},
        )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router", "admin_router"]
