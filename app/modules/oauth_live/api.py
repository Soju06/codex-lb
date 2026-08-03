from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.core.audit.service import AuditService
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardBadRequestError, DashboardNotFoundError
from app.dependencies import OAuthLivePolicyContext, get_oauth_live_policy_context
from app.modules.oauth_live.schemas import OAuthLivePolicyResponse, OAuthLivePolicyUpdateRequest
from app.modules.oauth_live.service import OAuthLivePolicyAccountNotFoundError, OAuthLivePolicyValidationError

router = APIRouter(
    prefix="/api/accounts/{account_id}/oauth-live-policy",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=OAuthLivePolicyResponse)
async def get_oauth_live_policy(
    account_id: str,
    context: OAuthLivePolicyContext = Depends(get_oauth_live_policy_context),
) -> OAuthLivePolicyResponse:
    try:
        return await context.service.get_policy(account_id)
    except OAuthLivePolicyAccountNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="account_not_found") from exc


@router.put("", response_model=OAuthLivePolicyResponse)
async def update_oauth_live_policy(
    request: Request,
    account_id: str,
    payload: OAuthLivePolicyUpdateRequest = Body(...),
    _write_access=Depends(require_dashboard_write_access),
    context: OAuthLivePolicyContext = Depends(get_oauth_live_policy_context),
) -> OAuthLivePolicyResponse:
    try:
        updated = await context.service.update_policy(account_id, payload)
    except OAuthLivePolicyAccountNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="account_not_found") from exc
    except OAuthLivePolicyValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_oauth_live_policy") from exc
    AuditService.log_async(
        "oauth_live_policy_updated",
        actor_ip=request.client.host if request.client else None,
        details={
            "caller_account_id": updated.caller_account_id,
            "is_active": updated.is_active,
            "allowed_account_count": len(updated.allowed_account_ids),
        },
    )
    return updated
