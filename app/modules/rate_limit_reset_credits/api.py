from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import AuditService
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.auth.refresh import RefreshError
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditFetchError,
    ResetCreditItem,
    ResetCreditsResponse,
    build_snapshot,
    consume_reset_credit,
    fetch_reset_credits,
)
from app.core.crypto import TokenEncryptor
from app.core.exceptions import (
    DashboardAuthError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardPermissionError,
    DashboardServiceUnavailableError,
)
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.db.models import Account, AccountStatus
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.auth_manager import AuthManager
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)
from app.modules.shared.schemas import DashboardModel
from app.modules.usage.updater import _resolve_upstream_route_for_account

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/accounts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)

FetchFn = Callable[..., Awaitable[ResetCreditsResponse]]
ConsumeFn = Callable[..., Awaitable[ConsumeResetCreditResponse]]
RefreshUsageFn = Callable[[Account], Awaitable[None]]
ResolveRouteFn = Callable[[Account], Awaitable[ResolvedUpstreamRoute | None]]

_NON_REDEEMABLE_STATUSES = frozenset({AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED})

_redeem_locks: dict[str, asyncio.Lock] = {}
_redeem_locks_registry_lock = asyncio.Lock()


class ResetCreditItemResponse(DashboardModel):
    id: str
    reset_type: str | None = None
    status: str | None = None
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    title: str | None = None
    description: str | None = None
    redeem_started_at: datetime | None = None
    redeemed_at: datetime | None = None


class RateLimitResetCreditsSnapshotResponse(DashboardModel):
    available_count: int = 0
    nearest_expires_at: datetime | None = None
    credits: list[ResetCreditItemResponse] = Field(default_factory=list)


class ConsumeResetCreditResponseSchema(DashboardModel):
    code: str | None = None
    windows_reset: int | None = None
    redeemed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _RedeemResetCreditOutcome:
    response: ConsumeResetCreditResponseSchema
    available_count_before: int
    available_count_after: int


@router.get(
    "/{account_id}/rate-limit-reset-credits",
    response_model=RateLimitResetCreditsSnapshotResponse | None,
)
async def get_rate_limit_reset_credits(
    account_id: str,
    context: AccountsContext = Depends(get_accounts_context),
) -> RateLimitResetCreditsSnapshotResponse | None:
    store = get_rate_limit_reset_credits_store()
    account = await context.repository.get_by_id(account_id)
    if account is None:
        await store.invalidate(account_id)
        return None
    if account.status in _NON_REDEEMABLE_STATUSES or not account.chatgpt_account_id:
        await store.invalidate(account_id)
        return None

    snapshot = store.get(account_id)
    if snapshot is not None:
        return _snapshot_to_response(snapshot)

    return None


@router.post(
    "/{account_id}/rate-limit-reset-credits/consume",
    response_model=ConsumeResetCreditResponseSchema,
)
async def consume_rate_limit_reset_credit(
    request: Request,
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> ConsumeResetCreditResponseSchema:
    account = await context.repository.get_by_id(account_id)
    if account is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")

    store = get_rate_limit_reset_credits_store()

    try:
        outcome = await _redeem_soonest_reset_credit(
            account=account,
            store=store,
            encryptor=TokenEncryptor(),
            lock_session=getattr(context, "session", None),
            auth_manager=context.service._auth_manager,
            refresh_usage=_build_refresh_usage_callback(context),
            resolve_route=_resolve_reset_credit_route,
        )
    except RefreshError as exc:
        if exc.is_permanent:
            get_account_selection_cache().invalidate()
        raise DashboardConflictError(
            f"Reset credit consume could not refresh account credentials: {exc.message}",
            code="account_reset_credit_refresh_failed",
        ) from exc
    except UpstreamProxyRouteError as exc:
        raise DashboardServiceUnavailableError(
            f"Reset credit consume upstream proxy route unavailable: {exc.reason}",
            code="account_reset_credit_upstream_route_unavailable",
        ) from exc

    AuditService.log_async(
        "account_rate_limit_reset_credit_consumed",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": account_id,
            "consume_code": outcome.response.code,
            "windows_reset": outcome.response.windows_reset,
            "available_reset_credits_before": outcome.available_count_before,
            "available_reset_credits_after": outcome.available_count_after,
        },
    )
    return outcome.response


async def _redeem_soonest_reset_credit(
    *,
    account: Account,
    store: RateLimitResetCreditsStore,
    encryptor: TokenEncryptor,
    lock_session: AsyncSession | None = None,
    fetch_fn: FetchFn | None = None,
    consume_fn: ConsumeFn | None = None,
    auth_manager: AuthManager | None = None,
    refresh_usage: RefreshUsageFn | None = None,
    resolve_route: ResolveRouteFn | None = None,
) -> _RedeemResetCreditOutcome:
    _assert_account_can_redeem_reset_credit(account)
    effective_fetch_fn = fetch_fn or fetch_reset_credits
    effective_consume_fn = consume_fn or consume_reset_credit

    async with serialize_reset_credit_redeem(account.id, session=lock_session):
        return await _redeem_soonest_reset_credit_locked(
            account=account,
            store=store,
            encryptor=encryptor,
            effective_fetch_fn=effective_fetch_fn,
            effective_consume_fn=effective_consume_fn,
            auth_manager=auth_manager,
            refresh_usage=refresh_usage,
            resolve_route=resolve_route,
        )


@asynccontextmanager
async def serialize_reset_credit_redeem(
    account_id: str,
    *,
    session: AsyncSession | None,
):
    if session is not None and session.get_bind().dialect.name == "postgresql":
        await _acquire_postgresql_reset_credit_redeem_lock(session, account_id)
        yield
        return

    # SQLite and direct unit-test callers keep the existing in-process lock.
    lock = await get_reset_credit_redeem_lock(account_id)
    async with lock:
        yield


async def _acquire_postgresql_reset_credit_redeem_lock(session: AsyncSession, account_id: str) -> None:
    lock_key = f"reset-credit-redeem:{account_id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": lock_key},
    )


async def _redeem_soonest_reset_credit_locked(
    *,
    account: Account,
    store: RateLimitResetCreditsStore,
    encryptor: TokenEncryptor,
    effective_fetch_fn: FetchFn,
    effective_consume_fn: ConsumeFn,
    auth_manager: AuthManager | None,
    refresh_usage: RefreshUsageFn | None,
    resolve_route: ResolveRouteFn | None,
) -> _RedeemResetCreditOutcome:
    redeem_account = account
    if auth_manager is not None:
        redeem_account = await auth_manager.ensure_fresh(account, force=False)

    cached_snapshot = store.get(account.id)
    cached_credit = _select_soonest_available_credit(cached_snapshot)
    if cached_credit is None:
        raise DashboardConflictError("No available reset credit", code="no_available_reset_credit")

    access_token = encryptor.decrypt(redeem_account.access_token_encrypted)
    route: ResolvedUpstreamRoute | None = None
    if resolve_route is not None:
        route = await resolve_route(redeem_account)

    try:
        credits_response = await effective_fetch_fn(
            access_token,
            redeem_account.chatgpt_account_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except ResetCreditFetchError as exc:
        raise _translate_fetch_error(exc) from exc

    credit = _select_soonest_available_credit_from_response(credits_response)
    if credit is None:
        await store.set(account.id, build_snapshot(credits_response))
        raise DashboardConflictError("No available reset credit", code="no_available_reset_credit")

    try:
        result = await effective_consume_fn(
            access_token,
            redeem_account.chatgpt_account_id,
            credit.id,
            route=route,
            allow_direct_egress=route is None,
        )
    except ConsumeResetCreditError as exc:
        raise _translate_consume_error(exc) from exc

    redeemed_at = result.credit.redeemed_at if result.credit else None
    available_count_after = max(0, credits_response.available_count - 1)
    await store.invalidate(account.id)

    if refresh_usage is not None:
        try:
            await refresh_usage(redeem_account)
        except Exception:
            logger.warning(
                "Reset credit consume succeeded but usage refresh failed account_id=%s",
                account.id,
                exc_info=True,
            )
            await _try_restore_reset_credits_snapshot_after_consume(
                account=account,
                redeem_account=redeem_account,
                encryptor=encryptor,
                store=store,
                fetch_fn=effective_fetch_fn,
                resolve_route=resolve_route,
            )

    return _RedeemResetCreditOutcome(
        response=ConsumeResetCreditResponseSchema(
            code=result.code,
            windows_reset=result.windows_reset,
            redeemed_at=redeemed_at,
        ),
        available_count_before=credits_response.available_count,
        available_count_after=available_count_after,
    )


def _assert_account_can_redeem_reset_credit(account: Account) -> None:
    if account.status in _NON_REDEEMABLE_STATUSES or not account.chatgpt_account_id:
        msg = (
            f"Account is {account.status.value} and cannot redeem a reset credit"
            if account.status in _NON_REDEEMABLE_STATUSES
            else "Account has no ChatGPT account ID and cannot redeem a reset credit"
        )
        raise DashboardConflictError(
            msg,
            code="account_not_reset_credit_applicable",
        )


def _build_refresh_usage_callback(context: AccountsContext) -> RefreshUsageFn | None:
    usage_updater = context.service._usage_updater
    if usage_updater is None:
        return None

    async def refresh_usage(account: Account) -> None:
        refreshed = await usage_updater.force_refresh(account)
        if not refreshed:
            raise RuntimeError(f"Forced usage refresh returned no update for account {account.id}")
        get_account_selection_cache().invalidate()

    return refresh_usage


async def _resolve_reset_credit_route(account: Account) -> ResolvedUpstreamRoute | None:
    return await _resolve_upstream_route_for_account(account, operation="rate_limit_reset_consume")


async def _try_restore_reset_credits_snapshot_after_consume(
    *,
    account: Account,
    redeem_account: Account,
    encryptor: TokenEncryptor,
    store: RateLimitResetCreditsStore,
    fetch_fn: FetchFn,
    resolve_route: ResolveRouteFn | None,
) -> None:
    """Best-effort cache repopulation when usage refresh fails after a successful consume."""
    try:
        access_token = encryptor.decrypt(redeem_account.access_token_encrypted)
        route: ResolvedUpstreamRoute | None = None
        if resolve_route is not None:
            route = await resolve_route(redeem_account)
        credits_response = await fetch_fn(
            access_token,
            redeem_account.chatgpt_account_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except Exception:
        logger.warning(
            "Reset credit consume post-refresh re-fetch failed account_id=%s",
            account.id,
            exc_info=True,
        )
        return
    await store.set(account.id, build_snapshot(credits_response))


async def get_reset_credit_redeem_lock(account_id: str) -> asyncio.Lock:
    lock = _redeem_locks.get(account_id)
    if lock is not None:
        return lock
    async with _redeem_locks_registry_lock:
        lock = _redeem_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _redeem_locks[account_id] = lock
        return lock


def _translate_fetch_error(exc: ResetCreditFetchError) -> Exception:
    if exc.status_code == 401:
        return DashboardAuthError(exc.message, code=exc.code)
    if exc.status_code == 403:
        return DashboardPermissionError(exc.message, code=exc.code)
    if exc.status_code == 409:
        return DashboardConflictError(exc.message, code=exc.code)
    return DashboardServiceUnavailableError(exc.message, code=exc.code)


def _translate_consume_error(exc: ConsumeResetCreditError) -> Exception:
    if exc.status_code == 401:
        return DashboardAuthError(exc.message, code=exc.code)
    if exc.status_code == 403:
        return DashboardPermissionError(exc.message, code=exc.code)
    if exc.status_code == 409:
        return DashboardConflictError(exc.message, code=exc.code)
    return DashboardServiceUnavailableError(exc.message, code=exc.code)


def _select_soonest_available_credit(
    snapshot: RateLimitResetCreditsSnapshot | None,
) -> ResetCreditItem | None:
    if snapshot is None:
        return None
    return _select_soonest_available_credit_from_items(snapshot.credits, snapshot.available_count)


def _select_soonest_available_credit_from_response(
    response: ResetCreditsResponse,
) -> ResetCreditItem | None:
    return _select_soonest_available_credit_from_items(response.credits, response.available_count)


def _select_available_credit_by_id(
    response: ResetCreditsResponse,
    credit_id: str,
) -> ResetCreditItem | None:
    if response.available_count <= 0:
        return None
    for credit in response.credits:
        if credit.id == credit_id and credit.status == "available":
            return credit
    return None


def _select_soonest_available_credit_from_items(
    credits: list[ResetCreditItem],
    available_count: int,
) -> ResetCreditItem | None:
    if available_count <= 0:
        return None
    available = [credit for credit in credits if credit.status == "available"]
    if not available:
        return None
    far_future = datetime.max.replace(tzinfo=timezone.utc)
    return min(available, key=lambda credit: credit.expires_at or far_future)


def _snapshot_to_response(
    snapshot: RateLimitResetCreditsSnapshot | None,
) -> RateLimitResetCreditsSnapshotResponse | None:
    if snapshot is None:
        return None
    return RateLimitResetCreditsSnapshotResponse(
        available_count=snapshot.available_count,
        nearest_expires_at=snapshot.nearest_expires_at,
        credits=[ResetCreditItemResponse.model_validate(credit.model_dump()) for credit in snapshot.credits],
    )
