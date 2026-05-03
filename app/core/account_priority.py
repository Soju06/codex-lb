from __future__ import annotations

from enum import Enum


class AccountPriority(str, Enum):
    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"


_PRIORITY_ORDER: dict[AccountPriority, int] = {
    AccountPriority.GOLD: 0,
    AccountPriority.SILVER: 1,
    AccountPriority.BRONZE: 2,
}


def coerce_account_priority(value: str | AccountPriority | None) -> AccountPriority:
    if isinstance(value, AccountPriority):
        return value
    normalized = (value or "").strip().lower()
    if normalized == AccountPriority.GOLD.value:
        return AccountPriority.GOLD
    if normalized == AccountPriority.BRONZE.value:
        return AccountPriority.BRONZE
    return AccountPriority.SILVER


def account_priority_rank(value: str | AccountPriority | None) -> int:
    return _PRIORITY_ORDER[coerce_account_priority(value)]
