"""Persist classified upstream holds under the account's existing lock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.balancer import handle_quota_exceeded, handle_rate_limit
from app.core.balancer.types import UpstreamError
from app.db.models import Account

if TYPE_CHECKING:
    from app.modules.proxy.load_balancer import LoadBalancer


async def mark_rate_limit(
    self: LoadBalancer,
    account: Account,
    error: UpstreamError,
    *,
    rejected_model: str | None = None,
    rejected_service_tier: str | None = None,
) -> None:
    lock = await self._get_account_lock(account.id)
    async with lock:
        state = self._state_for(account)
        handle_rate_limit(state, error)
        self._sync_runtime_state(account, state)
        async with self._repo_factory() as repos:
            await self._persist_state(
                repos.accounts,
                account,
                state,
                force_rejection=True,
                rejected_model=rejected_model,
                rejected_service_tier=rejected_service_tier,
            )
        self._selection_inputs_cache.invalidate()


async def mark_quota_exceeded(
    self: LoadBalancer,
    account: Account,
    error: UpstreamError,
    *,
    rejected_model: str | None = None,
    rejected_service_tier: str | None = None,
) -> None:
    lock = await self._get_account_lock(account.id)
    async with lock:
        state = self._state_for(account)
        handle_quota_exceeded(state, error)
        self._sync_runtime_state(account, state)
        async with self._repo_factory() as repos:
            await self._persist_state(
                repos.accounts,
                account,
                state,
                force_rejection=True,
                rejected_model=rejected_model,
                rejected_service_tier=rejected_service_tier,
            )
        self._selection_inputs_cache.invalidate()
