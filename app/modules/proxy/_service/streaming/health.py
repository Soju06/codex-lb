"""Keep owner-recovery health writes behind keyed reservation settlement."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.balancer.types import UpstreamError
from app.core.openai.requests import ResponsesRequest
from app.db.models import Account
from app.modules.proxy._service.streaming.protocol import _StreamingServiceProtocol
from app.modules.proxy._service.support import _StreamSettlement


@dataclass(frozen=True)
class OwnerRecoveryHealth:
    proxy: _StreamingServiceProtocol
    account: Account
    payload: ResponsesRequest
    settlement: _StreamSettlement
    keyed: bool

    async def __call__(self, error: UpstreamError, code: str | None) -> None:
        if code is None:
            return
        if self.keyed:
            self.settlement.error_code = code
            self.settlement.account_health_error = True
            self.settlement.settlement_order_required = True
            return
        await self.proxy._handle_stream_error(
            self.account,
            error,
            code,
            rejected_model=self.payload.model,
            rejected_service_tier=self.payload.service_tier,
        )
