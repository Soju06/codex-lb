from __future__ import annotations

from app.modules.accounts.schemas import AccountSummary
from app.modules.fleet.schemas import FleetAccountSummary, FleetWindowSummary


def fleet_account_summary_from_account(account: AccountSummary, *, include_usage: bool = True) -> FleetAccountSummary:
    """Project a dashboard account into the minimal fleet payload."""

    usage = account.usage
    return FleetAccountSummary(
        account_id=account.account_id,
        display_name=account.display_name,
        email=account.email,
        status=account.status,
        plan_type=account.plan_type,
        primary=FleetWindowSummary(
            remaining_percent=usage.primary_remaining_percent if include_usage and usage is not None else None,
            reset_at=account.reset_at_primary if include_usage else None,
            window_minutes=account.window_minutes_primary if include_usage else None,
        ),
        secondary=FleetWindowSummary(
            remaining_percent=usage.secondary_remaining_percent if include_usage and usage is not None else None,
            reset_at=account.reset_at_secondary if include_usage else None,
            window_minutes=account.window_minutes_secondary if include_usage else None,
        ),
        last_refresh_at=account.last_refresh_at if include_usage else None,
    )


def build_fleet_account_summaries(
    accounts: list[AccountSummary],
    *,
    include_usage: bool = True,
) -> list[FleetAccountSummary]:
    return [fleet_account_summary_from_account(account, include_usage=include_usage) for account in accounts]
