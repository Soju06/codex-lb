from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from app.core.utils.sse import parse_sse_data_json
from app.db.models import Account, AccountStatus


@dataclass(frozen=True)
class ProbeOutcome:
    http_status: int
    completed: bool = False


@dataclass(frozen=True)
class ProbeHold:
    generation: int
    status: AccountStatus
    reset_at: int | None
    blocked_at: int | None
    reason: str | None
    refresh_token: bytes
    access_token: bytes
    id_token: bytes
    account_identity: str | None
    user_identity: str | None
    model: str | None
    service_tier: str | None

    @classmethod
    def capture(cls, account: Account) -> ProbeHold:
        return cls(
            account.block_generation,
            account.status,
            account.reset_at,
            account.blocked_at,
            account.deactivation_reason,
            account.refresh_token_encrypted,
            account.access_token_encrypted,
            account.id_token_encrypted,
            account.chatgpt_account_id,
            account.chatgpt_user_id,
            account.rejected_model,
            account.rejected_service_tier,
        )

    def matches(self, model: str, service_tier: str | None) -> bool:
        return (
            self.status in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)
            and self.model is not None
            and self.model == model
            and self.service_tier == service_tier
        )


async def completed_probe_response(response: aiohttp.ClientResponse) -> ProbeOutcome:
    status = response.status
    if status != 200:
        return ProbeOutcome(status)
    event = bytearray()
    total = 0
    async for line in response.content:
        total += len(line)
        if total > 1024 * 1024 or len(event) + len(line) > 64 * 1024:
            return ProbeOutcome(status)
        event.extend(line)
        if line.strip():
            continue
        try:
            payload = parse_sse_data_json(event.decode("utf-8"))
        except UnicodeDecodeError:
            return ProbeOutcome(status)
        has_data = any(line.startswith(b"data:") for line in event.splitlines())
        event.clear()
        if payload is None:
            if has_data:
                return ProbeOutcome(status)
            continue
        kind = payload.get("type")
        if kind in ("response.failed", "response.incomplete", "error"):
            return ProbeOutcome(status)
        if kind == "response.completed":
            body = payload.get("response")
            return ProbeOutcome(
                status,
                isinstance(body, dict)
                and body.get("status") == "completed"
                and isinstance(body.get("id"), str)
                and bool(body.get("id"))
                and body.get("error") is None,
            )
    return ProbeOutcome(status)
