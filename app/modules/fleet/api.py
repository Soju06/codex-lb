from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Security

from app.core.auth.dependencies import set_dashboard_error_format, validate_usage_api_key
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardServiceUnavailableError
from app.core.shutdown import is_control_plane_task_admission_open, wait_for_tasks_to_drain
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.db.session import get_background_session
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.service import ApiKeyData
from app.modules.fleet.mappers import build_fleet_account_summaries
from app.modules.fleet.observability import build_fleet_observability
from app.modules.fleet.schemas import FleetObservabilityResponse, FleetRefreshResponse, FleetSummaryResponse
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/fleet",
    tags=["fleet"],
    dependencies=[Depends(set_dashboard_error_format)],
)

_REFRESH_SKIP_STATUSES = {AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED}
# Every route-owned refresh is registered at creation. Caller cancellation only
# changes who observes its outcome; it does not establish task ownership.
_BACKGROUND_REFRESH_TASKS: set[asyncio.Task[FleetRefreshResponse]] = set()


def _visible_account_ids(api_key: ApiKeyData) -> list[str] | None:
    return list(api_key.assigned_account_ids) if api_key.account_assignment_scope_enabled else None


def _usage_sections(raw: str) -> set[str]:
    if not raw or not raw.strip():
        return set()
    return {section.strip() for section in raw.split(",") if section.strip()}


async def _can_view_fleet_usage(api_key: ApiKeyData) -> bool:
    sections = _usage_sections(api_key.usage_sections)
    if "account_pool_usage" not in sections or "upstream_limits" not in sections:
        return False
    settings = await get_settings_cache().get()
    return not bool(getattr(settings, "hide_upstream_quota_from_api_keys", False))


@router.get("/summary", response_model=FleetSummaryResponse)
async def get_fleet_summary(
    context: AccountsContext = Depends(get_accounts_context),
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> FleetSummaryResponse:
    """Read-only, minimal per-account capacity summary for fleet consumers."""

    visible_account_ids = _visible_account_ids(api_key)
    include_usage = await _can_view_fleet_usage(api_key)
    accounts = await context.service.list_accounts(account_ids=visible_account_ids)
    persisted_status_by_account_id: dict[str, str] | None = None
    if not include_usage:
        persisted_accounts = (
            await context.repository.list_accounts_by_ids(visible_account_ids, refresh_existing=True)
            if visible_account_ids is not None
            else await context.repository.list_accounts(refresh_existing=True)
        )
        persisted_status_by_account_id = {account.id: account.status.value for account in persisted_accounts}
    return FleetSummaryResponse(
        accounts=build_fleet_account_summaries(
            accounts,
            include_usage=include_usage,
            persisted_status_by_account_id=persisted_status_by_account_id,
        )
    )


@router.get("/observability", response_model=FleetObservabilityResponse)
async def get_fleet_observability(
    context: AccountsContext = Depends(get_accounts_context),
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> FleetObservabilityResponse:
    """Read-only pressure and sticky-session summary for fleet consumers."""

    return await build_fleet_observability(
        context.session,
        visible_account_ids=_visible_account_ids(api_key),
        include_usage=await _can_view_fleet_usage(api_key),
    )


@router.post("/refresh", response_model=FleetRefreshResponse)
async def refresh_fleet_usage(
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> FleetRefreshResponse:
    """Request a bounded usage refresh using codex-lb's normal refresh rules."""

    if not is_control_plane_task_admission_open():
        raise DashboardServiceUnavailableError("Server is draining")
    task = asyncio.create_task(
        _refresh_fleet_usage_with_owned_session(_visible_account_ids(api_key)),
        name="fleet-usage-refresh",
    )
    _BACKGROUND_REFRESH_TASKS.add(task)
    task.add_done_callback(_discard_refresh_task)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_handle_cancelled_refresh_task_done)
        raise


async def _refresh_fleet_usage_with_owned_session(visible_account_ids: list[str] | None) -> FleetRefreshResponse:
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
        ).refresh_accounts(eligible_accounts, latest_primary, own_singleflight_sessions=True)
        if usage_written:
            await get_rate_limit_headers_cache().invalidate()
            get_account_selection_cache().invalidate()
        return FleetRefreshResponse(
            usage_written=usage_written,
            account_count=len(accounts),
            attempted_count=len(eligible_accounts),
            generated_at=utcnow(),
        )


async def drain_background_refresh_tasks(timeout_seconds: float) -> bool:
    pending = await wait_for_tasks_to_drain(_BACKGROUND_REFRESH_TASKS, timeout_seconds)
    for task in sorted(pending, key=lambda pending_task: pending_task.get_name()):
        logger.warning("Fleet refresh task did not drain before shutdown: %s", task.get_name())
    return not pending


def _handle_cancelled_refresh_task_done(task: asyncio.Task[FleetRefreshResponse]) -> None:
    try:
        _log_cancelled_refresh_task_exception(task)
    finally:
        _BACKGROUND_REFRESH_TASKS.discard(task)


def _discard_refresh_task(task: asyncio.Task[FleetRefreshResponse]) -> None:
    _BACKGROUND_REFRESH_TASKS.discard(task)


def _log_cancelled_refresh_task_exception(task: asyncio.Task[FleetRefreshResponse]) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error(
            "Fleet usage refresh failed after request cancellation",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
