from __future__ import annotations

from datetime import datetime

import anyio

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot, ResetCreditItem


class RateLimitResetCreditsStore:
    """In-memory cache of the most recent reset-credits snapshot per account.

    Mirrors the lock-guarded shape of :class:`RateLimitHeadersCache` /
    :class:`AccountSelectionCache`. Snapshots are keyed by account id and are
    repopulated by each replica's refresh scheduler on every tick; reads from
    the dashboard (GET + the AccountSummary mapper) never hit upstream.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, RateLimitResetCreditsSnapshot] = {}
        self._pending_redeems: dict[tuple[str, str], str] = {}
        self._lock = anyio.Lock()
        self._clear_generation = 0
        self._account_generations: dict[str, int] = {}

    async def set(self, account_id: str, snapshot: RateLimitResetCreditsSnapshot) -> None:
        async with self._lock:
            self._snapshots[account_id] = snapshot
            self._bump_account_generation(account_id)

    def generation(self, account_id: str) -> int:
        return self._generation_for(account_id)

    async def set_if_generation(
        self,
        account_id: str,
        snapshot: RateLimitResetCreditsSnapshot,
        expected_generation: int,
    ) -> bool:
        async with self._lock:
            if self._generation_for(account_id) != expected_generation:
                return False
            self._snapshots[account_id] = snapshot
            self._bump_account_generation(account_id)
            return True

    async def mark_credit_redeemed(
        self,
        account_id: str,
        credit_id: str,
        *,
        redeemed_at: datetime | None,
    ) -> None:
        async with self._lock:
            snapshot = self._snapshots.get(account_id)
            if snapshot is None:
                return
            updated_credits, matched = _mark_credit_redeemed(snapshot.credits, credit_id, redeemed_at=redeemed_at)
            if not matched:
                return
            available_count = sum(1 for credit in updated_credits if credit.status == "available")
            self._snapshots[account_id] = snapshot.model_copy(
                update={
                    "available_count": available_count,
                    "nearest_expires_at": _nearest_available_expires_at(updated_credits),
                    "credits": updated_credits,
                }
            )
            self._bump_account_generation(account_id)

    def get(self, account_id: str) -> RateLimitResetCreditsSnapshot | None:
        return self._snapshots.get(account_id)

    async def remember_redeem_request(self, account_id: str, redeem_request_id: str, credit_id: str) -> None:
        async with self._lock:
            self._pending_redeems[(account_id, redeem_request_id)] = credit_id

    def get_redeem_request_credit_id(self, account_id: str, redeem_request_id: str) -> str | None:
        return self._pending_redeems.get((account_id, redeem_request_id))

    async def invalidate(self, account_id: str | None = None) -> None:
        async with self._lock:
            if account_id is None:
                self._snapshots.clear()
                self._pending_redeems.clear()
                self._clear_generation += 1
                return
            self._snapshots.pop(account_id, None)
            self._bump_account_generation(account_id)

    def _generation_for(self, account_id: str) -> int:
        return self._clear_generation + self._account_generations.get(account_id, 0)

    def _bump_account_generation(self, account_id: str) -> None:
        self._account_generations[account_id] = self._account_generations.get(account_id, 0) + 1


_rate_limit_reset_credits_store = RateLimitResetCreditsStore()


def get_rate_limit_reset_credits_store() -> RateLimitResetCreditsStore:
    return _rate_limit_reset_credits_store


def _mark_credit_redeemed(
    credits: list[ResetCreditItem],
    credit_id: str,
    *,
    redeemed_at: datetime | None,
) -> tuple[list[ResetCreditItem], bool]:
    matched = False
    updated: list[ResetCreditItem] = []
    for credit in credits:
        if credit.id != credit_id:
            updated.append(credit)
            continue
        matched = True
        updated.append(credit.model_copy(update={"status": "redeemed", "redeemed_at": redeemed_at}))
    return updated, matched


def _nearest_available_expires_at(credits: list[ResetCreditItem]) -> datetime | None:
    candidates = [
        credit.expires_at for credit in credits if credit.status == "available" and credit.expires_at is not None
    ]
    return min(candidates) if candidates else None
