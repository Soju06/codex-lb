from __future__ import annotations

import anyio

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot


class RateLimitResetCreditsStore:
    """In-memory cache of the most recent reset-credits snapshot per account.

    Mirrors the lock-guarded shape of :class:`RateLimitHeadersCache` /
    :class:`AccountSelectionCache`. Snapshots are keyed by account id and are
    repopulated by the leader-gated refresh scheduler on each tick; reads from
    the dashboard (GET + the AccountSummary mapper) never hit upstream.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, RateLimitResetCreditsSnapshot] = {}
        self._lock = anyio.Lock()

    async def set(self, account_id: str, snapshot: RateLimitResetCreditsSnapshot) -> None:
        async with self._lock:
            self._snapshots[account_id] = snapshot

    def get(self, account_id: str) -> RateLimitResetCreditsSnapshot | None:
        return self._snapshots.get(account_id)

    async def invalidate(self, account_id: str | None = None) -> None:
        async with self._lock:
            if account_id is None:
                self._snapshots.clear()
                return
            self._snapshots.pop(account_id, None)


_rate_limit_reset_credits_store = RateLimitResetCreditsStore()


def get_rate_limit_reset_credits_store() -> RateLimitResetCreditsStore:
    return _rate_limit_reset_credits_store
