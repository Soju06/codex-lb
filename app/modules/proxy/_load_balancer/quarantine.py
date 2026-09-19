from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.core.balancer import PERMANENT_FAILURE_CODES
from app.db.models import Account
from app.modules.proxy.account_cache import (
    is_account_locally_routing_unavailable,
    mark_account_routing_unavailable_pending_persist,
    propagate_account_routing_change,
)


def apply_local_routing_quarantine(excluded_ids: set[str], accounts: Collection[Account]) -> None:
    """Exclude accounts quarantined locally while the routing snapshot catches up."""
    excluded_ids.update(account.id for account in accounts if is_account_locally_routing_unavailable(account.id))


async def quarantine_permanent_failure(load_balancer: Any, account: Account, error_code: str) -> bool:
    """Publish a guarded routing quarantine before keyed-stream settlement."""
    reason = PERMANENT_FAILURE_CODES.get(error_code)
    if reason is None:
        return False
    lock = await load_balancer._get_account_lock(account.id)
    async with lock:
        async with load_balancer._repo_factory() as repos:
            quarantined = await repos.accounts.update_status_if_current(
                account.id,
                account.status,
                reason,
                account.reset_at,
                blocked_at=account.blocked_at,
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
                expected_refresh_token_encrypted=account.refresh_token_encrypted,
            )
        if quarantined:
            account.deactivation_reason = reason
            mark_account_routing_unavailable_pending_persist(account.id)
            load_balancer._selection_inputs_cache.invalidate()
            await propagate_account_routing_change()
        return quarantined
