from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.core.audit.service import AuditService
from app.core.auth.dependencies import set_openai_error_format
from app.core.clients.rate_limit_reset_credits import ConsumeResetCreditResponse, ResetCreditsResponse
from app.dependencies import DesktopResetContext, get_desktop_reset_context
from app.modules.desktop_usage.identity import DesktopIdentity, desktop_identity

router = APIRouter(tags=["proxy"], dependencies=[Depends(set_openai_error_format)])


class ResetConsumeRequest(BaseModel):
    credit_id: str | None = Field(default=None, min_length=1, max_length=512)
    redeem_request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("/api/codex/desktop/reset-credits")
@router.get("/api/codex/desktop/reset-credits/", include_in_schema=False)
async def reset_credits(
    response: Response,
    identity: DesktopIdentity = Depends(desktop_identity),
    context: DesktopResetContext = Depends(get_desktop_reset_context),
) -> ResetCreditsResponse:
    response.headers["Cache-Control"] = "no-store"
    return await context.service.list_credits(identity)


@router.post("/api/codex/desktop/reset-credits/consume")
@router.post("/api/codex/desktop/reset-credits/consume/", include_in_schema=False)
async def consume_reset(
    request: Request,
    response: Response,
    payload: ResetConsumeRequest,
    identity: DesktopIdentity = Depends(desktop_identity),
    context: DesktopResetContext = Depends(get_desktop_reset_context),
) -> ConsumeResetCreditResponse:
    response.headers["Cache-Control"] = "no-store"
    result = await context.service.consume(identity, request_id=payload.redeem_request_id, credit_id=payload.credit_id)
    AuditService.log_async(
        "desktop_reset_credit_consume",
        actor_ip=request.client.host if request.client else None,
        details={
            "caller_account_id": identity.account_id,
            "outcome": result.code,
            "windows_reset": result.windows_reset,
        },
    )
    return result
