from __future__ import annotations

from fastapi import APIRouter, Depends, Security

from app.core.auth.dependencies import set_dashboard_error_format, validate_usage_api_key
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.db.session import get_background_session
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.service import ApiKeyData
from app.modules.fleet.mappers import build_fleet_account_summaries
from app.modules.fleet.schemas import FleetRefreshResponse, FleetSummaryResponse
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

router = APIRouter(
    prefix="/api/fleet",
    tags=["fleet"],
    dependencies=[Depends(set_dashboard_error_format)],
)

_REFRESH_SKIP_STATUSES = {AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED}


def _visible_account_ids(api_key: ApiKeyData) -> list[str] | None:
    return list(api_key.assigned_account_ids) if api_key.account_assignment_scope_enabled else None


@router.get("/summary", response_model=FleetSummaryResponse)
async def get_fleet_summary(
    context: AccountsContext = Depends(get_accounts_context),
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> FleetSummaryResponse:
    """Read-only, minimal per-account capacity summary for fleet consumers."""

    accounts = await context.service.list_accounts(account_ids=_visible_account_ids(api_key))
    return FleetSummaryResponse(accounts=build_fleet_account_summaries(accounts))


@router.post("/refresh", response_model=FleetRefreshResponse)
async def refresh_fleet_usage(
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> FleetRefreshResponse:
    """Request a bounded usage refresh using codex-lb's normal refresh rules."""

    visible_account_ids = _visible_account_ids(api_key)
    async with get_background_session() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_usage_repo = AdditionalUsageRepository(session)
        accounts = (
            await accounts_repo.list_accounts_by_ids(visible_account_ids, refresh_existing=True)
            if visible_account_ids is not None
            else await accounts_repo.list_accounts(refresh_existing=True)
        )
        eligible_accounts = [account for account in accounts if account.status not in _REFRESH_SKIP_STATUSES]
        latest_primary = await usage_repo.latest_by_account(window="primary", account_ids=visible_account_ids)
        usage_written = await UsageUpdater(
            usage_repo,
            accounts_repo,
            additional_usage_repo,
        ).refresh_accounts(eligible_accounts, latest_primary)
    if usage_written:
        await get_rate_limit_headers_cache().invalidate()
        get_account_selection_cache().invalidate()
    return FleetRefreshResponse(
        usage_written=usage_written,
        account_count=len(accounts),
        attempted_count=len(eligible_accounts),
        generated_at=utcnow(),
    )
