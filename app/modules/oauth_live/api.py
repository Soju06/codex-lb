from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.core.audit.service import AuditService
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardBadRequestError
from app.dependencies import OAuthLivePolicyContext, get_oauth_live_policy_context
from app.modules.oauth_live.schemas import OAuthLivePolicyResponse, OAuthLivePolicyUpdateRequest
from app.modules.oauth_live.service import OAuthLivePolicyValidationError

router = APIRouter(
    prefix="/api/oauth-live-policy",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=OAuthLivePolicyResponse)
async def get_oauth_live_policy(
    context: OAuthLivePolicyContext = Depends(get_oauth_live_policy_context),
) -> OAuthLivePolicyResponse:
    return await context.service.get_policy()


@router.put("", response_model=OAuthLivePolicyResponse)
async def update_oauth_live_policy(
    request: Request,
    payload: OAuthLivePolicyUpdateRequest = Body(...),
    _write_access=Depends(require_dashboard_write_access),
    context: OAuthLivePolicyContext = Depends(get_oauth_live_policy_context),
) -> OAuthLivePolicyResponse:
    try:
        updated = await context.service.update_policy(payload)
    except OAuthLivePolicyValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_oauth_live_policy") from exc
    AuditService.log_async(
        "oauth_live_policy_updated",
        actor_ip=request.client.host if request.client else None,
        details={
            "is_active": updated.is_active,
            "allowed_account_count": len(updated.allowed_account_ids),
        },
    )
    return updated
