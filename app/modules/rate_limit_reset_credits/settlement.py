"""Finish received receipts without repeating the upstream consume."""

from __future__ import annotations

import asyncio
import logging

from app.core.clients.rate_limit_reset_credits import ConsumeResetCreditResponse
from app.core.utils.shared_future import _await_cleanup_deferring_cancellation
from app.modules.rate_limit_reset_credits import outcomes
from app.modules.rate_limit_reset_credits.invalidation import publish_reset_credit_invalidation
from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore

logger = logging.getLogger(__name__)


async def settle_received_result(
    account_id: str,
    request_id: str,
    status: outcomes.RedeemOutcome,
    *,
    result: ConsumeResetCreditResponse,
    store: RateLimitResetCreditsStore,
    actor_ip: str | None = None,
) -> None:
    async def settle() -> None:
        for attempt in range(3):
            try:
                async with asyncio.timeout(5):
                    await outcomes.finish_attempt(account_id, request_id, status, result=result, actor_ip=actor_ip)
                break
            except Exception:
                if attempt == 2:
                    raise
                logger.warning("Retrying reset-credit receipt settlement account_id=%s", account_id, exc_info=True)
                await asyncio.sleep(0.1 * (attempt + 1))
        await store.invalidate(account_id)
        revision = await publish_reset_credit_invalidation(account_id)
        await store.acknowledge_revision(account_id, revision)

    cancellation = await _await_cleanup_deferring_cancellation(settle())
    if cancellation is not None:
        raise cancellation
