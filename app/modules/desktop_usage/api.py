from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_openai_error_format
from app.dependencies import (
    DesktopResetContext,
    DesktopUsageContext,
    get_desktop_reset_context,
    get_desktop_usage_context,
)
from app.modules.desktop_resets.projection import ResetPoolUnavailable
from app.modules.desktop_usage.composition import compose_desktop_usage
from app.modules.desktop_usage.identity import DesktopIdentity, desktop_identity

router = APIRouter(tags=["proxy"], dependencies=[Depends(set_openai_error_format)])


@router.get("/api/codex/desktop/usage")
@router.get("/api/codex/desktop/usage/", include_in_schema=False)
async def desktop_usage(
    identity: DesktopIdentity = Depends(desktop_identity),
    context: DesktopUsageContext = Depends(get_desktop_usage_context),
    resets: DesktopResetContext = Depends(get_desktop_reset_context),
) -> JSONResponse:
    pooled = await context.service.get_rate_limit_payload()
    original = identity.usage
    if await resets.service.enabled():
        original = dict(original)
        try:
            credits = await resets.service.list_credits(identity, cached_only=True)
            original["rate_limit_reset_credits"] = credits.model_dump(mode="json")
        except ResetPoolUnavailable:
            original.pop("rate_limit_reset_credits", None)
    return JSONResponse(compose_desktop_usage(original, pooled), headers={"Cache-Control": "no-store"})
