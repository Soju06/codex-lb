from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.core.auth.dependencies import validate_codex_usage_identity
from app.core.exceptions import ProxyAuthError, ProxyRequiredCapabilityTransportError, ProxyUpstreamError
from app.core.types import JsonObject
from app.core.usage.models import UsagePayload


@dataclass(frozen=True, repr=False)
class DesktopIdentity:
    account_id: str
    chatgpt_account_id: str
    usage: JsonObject


async def desktop_identity(request: Request) -> DesktopIdentity:
    if request.headers.getlist("X-Codex-LB-Required-Capability"):
        raise ProxyRequiredCapabilityTransportError()
    if not request.headers.get("chatgpt-account-id", "").strip():
        raise ProxyAuthError("ChatGPT authentication with chatgpt-account-id is required")
    await validate_codex_usage_identity(request)
    payload = request.state.codex_usage_identity_payload
    if not isinstance(payload, UsagePayload) or payload.raw_payload is None:
        raise ProxyUpstreamError("Original ChatGPT usage is unavailable", code="pooled_usage_unavailable")
    return DesktopIdentity(
        request.state.codex_usage_identity_account_id,
        request.state.codex_usage_identity_chatgpt_account_id,
        payload.raw_payload,
    )
