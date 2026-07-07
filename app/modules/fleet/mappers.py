from __future__ import annotations

from app.modules.accounts.schemas import AccountSummary
from app.modules.fleet.schemas import FleetAccountSummary, FleetWindowSummary


def fleet_account_summary_from_account(
    account: AccountSummary,
    *,
    include_usage: bool = True,
    persisted_status_by_account_id: dict[str, str] | None = None,
) -> FleetAccountSummary:
    """Project a dashboard account into the minimal fleet payload."""

    usage = account.usage
    if include_usage:
        status = account.status
    elif persisted_status_by_account_id is None:
        status = "unknown"
    else:
        status = persisted_status_by_account_id.get(account.account_id, "unknown")
    return FleetAccountSummary(
        account_id=account.account_id,
        display_name=account.display_name,
        email=account.email,
        status=status,
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
    persisted_status_by_account_id: dict[str, str] | None = None,
) -> list[FleetAccountSummary]:
    return [
        fleet_account_summary_from_account(
            account,
            include_usage=include_usage,
            persisted_status_by_account_id=persisted_status_by_account_id,
        )
        for account in accounts
    ]
