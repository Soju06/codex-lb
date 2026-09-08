from __future__ import annotations

from collections.abc import Mapping

from app.core.balancer import AccountState
from app.db.models import Account, UsageHistory
from app.modules.proxy.usage_caps import reached_usage_cap_resets

USAGE_CAP_ERROR_CODE = "account_usage_cap_reached"
USAGE_CAP_ERROR_MESSAGE = "Account usage cap reached"


def usage_cap_resets_by_account(
    accounts: list[Account],
    primary: Mapping[str, UsageHistory],
    secondary: Mapping[str, UsageHistory],
    *,
    now: float,
) -> dict[str, tuple[int | None, ...]]:
    return {
        account.id: resets
        for account in accounts
        if (resets := reached_usage_cap_resets(account, primary.get(account.id), secondary.get(account.id), now=now))
    }


def filter_usage_capped_states(
    states: list[AccountState],
    cap_resets_by_account: Mapping[str, tuple[int | None, ...]],
    *,
    now: float,
) -> list[AccountState]:
    return [
        state
        for state in states
        if not any(reset is None or reset > now for reset in cap_resets_by_account.get(state.account_id, ()))
    ]
