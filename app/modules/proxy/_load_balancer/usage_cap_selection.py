from __future__ import annotations

from collections.abc import Collection, Mapping

from app.core.balancer import AccountState
from app.db.models import Account, UsageHistory
from app.modules.proxy.usage_caps import reached_usage_cap_resets

USAGE_CAP_ERROR_CODE = "account_usage_cap_reached"
USAGE_CAP_ERROR_MESSAGE = "Account usage cap reached"


def usage_capped_account_ids(
    accounts: list[Account],
    primary: Mapping[str, UsageHistory],
    secondary: Mapping[str, UsageHistory],
    *,
    now: float,
) -> frozenset[str]:
    return frozenset(
        account.id
        for account in accounts
        if reached_usage_cap_resets(account, primary.get(account.id), secondary.get(account.id), now=now)
    )


def filter_usage_capped_states(
    states: list[AccountState],
    capped_account_ids: Collection[str],
) -> list[AccountState]:
    return [state for state in states if state.account_id not in capped_account_ids]
