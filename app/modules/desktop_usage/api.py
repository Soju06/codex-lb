from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_openai_error_format, validate_codex_usage_identity
from app.core.exceptions import ProxyAuthError, ProxyRequiredCapabilityTransportError, ProxyUpstreamError
from app.core.types import JsonObject
from app.core.usage.models import UsagePayload
from app.dependencies import DesktopUsageContext, get_desktop_usage_context
from app.modules.desktop_usage.composition import compose_desktop_usage

router = APIRouter(tags=["proxy"], dependencies=[Depends(set_openai_error_format)])


async def _original_usage(request: Request) -> JsonObject:
    if request.headers.getlist("X-Codex-LB-Required-Capability"):
        raise ProxyRequiredCapabilityTransportError()
    if not request.headers.get("chatgpt-account-id", "").strip():
        raise ProxyAuthError("ChatGPT authentication with chatgpt-account-id is required")
    await validate_codex_usage_identity(request)
    payload = request.state.codex_usage_identity_payload
    if not isinstance(payload, UsagePayload) or payload.raw_payload is None:
        raise ProxyUpstreamError("Original ChatGPT usage is unavailable", code="pooled_usage_unavailable")
    return payload.raw_payload


@router.get("/api/codex/desktop/usage")
@router.get("/api/codex/desktop/usage/", include_in_schema=False)
async def desktop_usage(
    original: JsonObject = Depends(_original_usage),
    context: DesktopUsageContext = Depends(get_desktop_usage_context),
) -> JSONResponse:
    pooled = await context.service.get_rate_limit_payload()
    return JSONResponse(
        compose_desktop_usage(original, pooled),
        headers={"Cache-Control": "no-store"},
    )
