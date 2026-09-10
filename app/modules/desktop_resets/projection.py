from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.clients.rate_limit_reset_credits import (
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.core.exceptions import ProxyUpstreamError
from app.db.models import Account, AccountStatus


class ResetPoolUnavailable(ProxyUpstreamError):
    def __init__(self, message: str = "Reset credit inventory is unavailable") -> None:
        super().__init__(message, code="pooled_reset_credits_unavailable")


@dataclass(frozen=True)
class OwnedCredit:
    owner_id: str
    credit: ResetCreditItem


def eligible(account: Account) -> bool:
    return (
        account.status not in {AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED}
        and account.delete_requested_at is None
        and bool(account.chatgpt_account_id)
    )


def _expiry(credit: ResetCreditItem) -> datetime:
    value = credit.expires_at or datetime.max.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def pool_credits(snapshots: dict[str, RateLimitResetCreditsSnapshot], *, now: datetime) -> list[OwnedCredit]:
    credits: list[OwnedCredit] = []
    seen: set[str] = set()
    for owner, snapshot in snapshots.items():
        available = [credit for credit in snapshot.credits if credit.status == "available"]
        if snapshot.available_count != len(available):
            raise ResetPoolUnavailable("Reset credit count disagrees with its inventory")
        for credit in available:
            if credit.id in seen:
                raise ResetPoolUnavailable("Reset credit ownership is ambiguous")
            seen.add(credit.id)
            if _expiry(credit) > now:
                credits.append(OwnedCredit(owner, credit))
    return sorted(credits, key=lambda item: (_expiry(item.credit), item.owner_id, item.credit.id))


def inventory(credits: list[OwnedCredit]) -> ResetCreditsResponse:
    return ResetCreditsResponse(available_count=len(credits), credits=[item.credit for item in credits])
