"""FastAPI router for the Claude OAuth link flow endpoints.

Three endpoints under ``/api/claude/oauth``:

- ``POST /api/claude/oauth/start``   — start a flow (write access required).
- ``GET  /api/claude/oauth/status``  — read flow status.
- ``POST /api/claude/oauth/callback`` — submit pasted code (write access required).

The business logic lives in :class:`ClaudeOAuthService`. This module is a
thin HTTP envelope: dashboard auth + write-access dependency injection,
status-code mapping for :class:`ClaudeOauthFlowError`, request/response
shape serialization.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.config.settings import get_settings
from app.core.exceptions import (
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardGoneError,
    DashboardNotFoundError,
    DashboardUpstreamError,
)
from app.db.session import get_session
from app.modules.claude.auth_manager import ClaudeAuthManager
from app.modules.claude.oauth.schemas import (
    ClaudeOauthCallbackRequest,
    ClaudeOauthCallbackResponse,
    ClaudeOauthStartResponse,
    ClaudeOauthStatusResponse,
)
from app.modules.claude.oauth.service import ClaudeOauthFlowError, ClaudeOAuthService
from app.modules.claude.repository import SqlClaudeAccountRepository

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/claude/oauth",
    tags=["dashboard-claude-oauth"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


# ---------------------------------------------------------------------------
# Dependency override seam
# ---------------------------------------------------------------------------


async def get_claude_oauth_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[ClaudeOAuthService]:
    """Build the default service for a request.

    Reuses the lifespan-provided :class:`ClaudeOAuthClient` (built by
    :func:`app.modules.claude.wiring.build_claude_oauth_client`) and the
    request-scoped ``SqlClaudeAccountRepository`` so callback persistence
    commits within the request transaction.

    Raises :class:`RuntimeError` when the lifespan has not configured the
    client — that is a wiring bug, not a runtime fallback. Tests override
    this dependency directly via ``app.dependency_overrides``.
    """
    settings = get_settings()
    repo = SqlClaudeAccountRepository(session)

    oauth_client = getattr(request.app.state, "claude_oauth_client", None)
    if oauth_client is None:
        # Lifespan did not provide a client. Fail loudly so this is fixed in
        # tests / production setup, not silently fall back to a placeholder.
        raise RuntimeError(
            "claude_oauth_client is not configured on app.state. "
            "Ensure app_lifespan creates a ClaudeOAuthClient "
            "(see app/modules/claude/wiring.py::build_claude_oauth_client)."
        )

    manager = ClaudeAuthManager(repo=repo)
    yield ClaudeOAuthService(
        settings=settings,
        oauth_client=oauth_client,
        auth_manager=manager,
        accounts_repo=repo,
    )


# ---------------------------------------------------------------------------
# Error code → dashboard exception
# ---------------------------------------------------------------------------


# Maps the service-level error_code to the right Dashboard*Error class so the
# project's exception middleware (app.core.handlers.exceptions) preserves the
# ``error.code`` in the JSON body. New error codes must be added here.
_ERROR_CODE_TO_EXC: dict[str, type] = {
    "flow_not_found": DashboardNotFoundError,
    "flow_expired": DashboardGoneError,
    "flow_not_pending": DashboardConflictError,
    "state_mismatch": DashboardBadRequestError,
    "missing_code": DashboardBadRequestError,
    "invalid_grant": DashboardUpstreamError,
    "anthropic_unreachable": DashboardUpstreamError,
    "id_token_missing": DashboardBadRequestError,
    "id_token_malformed": DashboardBadRequestError,
    "id_token_claims_incomplete": DashboardBadRequestError,
    "account_already_exists": DashboardConflictError,
}


def _to_dashboard_error(exc: ClaudeOauthFlowError) -> Exception:
    """Translate a service-layer error to a dashboard-envelope exception."""
    cls = _ERROR_CODE_TO_EXC.get(exc.code, DashboardBadRequestError)
    return cls(str(exc), code=exc.code)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/start", response_model=ClaudeOauthStartResponse)
async def start_oauth(
    _write=Depends(require_dashboard_write_access),
    service: ClaudeOAuthService = Depends(get_claude_oauth_service),
) -> ClaudeOauthStartResponse:
    return await service.start_oauth()


@router.get("/status", response_model=ClaudeOauthStatusResponse)
async def oauth_status(
    flowId: str = Query(..., alias="flowId"),
    service: ClaudeOAuthService = Depends(get_claude_oauth_service),
) -> ClaudeOauthStatusResponse:
    return await service.oauth_status(flowId)


@router.post("/callback", response_model=ClaudeOauthCallbackResponse)
async def callback_oauth(
    payload: ClaudeOauthCallbackRequest,
    session: AsyncSession = Depends(get_session),
    _write=Depends(require_dashboard_write_access),
    service: ClaudeOAuthService = Depends(get_claude_oauth_service),
) -> ClaudeOauthCallbackResponse:
    try:
        result = await service.complete_oauth(
            flow_id=payload.flow_id,
            code=payload.code,
            state=payload.state,
        )
    except ClaudeOauthFlowError as exc:
        raise _to_dashboard_error(exc) from exc

    # Persist the inserted account row (the repository.insert call inside
    # the auth_manager does not commit — the request boundary commits here
    # so success + failure share the same lifecycle).
    await session.commit()
    return result
