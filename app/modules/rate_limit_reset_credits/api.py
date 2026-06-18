from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import APIRouter, Depends
from pydantic import Field

from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
    consume_reset_credit,
)
from app.core.crypto import TokenEncryptor
from app.core.exceptions import (
    DashboardAuthError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardPermissionError,
    DashboardServiceUnavailableError,
)
from app.db.models import Account
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.rate_limit_reset_credits.store import (
    RateLimitResetCreditsStore,
    get_rate_limit_reset_credits_store,
)
from app.modules.shared.schemas import DashboardModel

router = APIRouter(
    prefix="/api/accounts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)

ConsumeFn = Callable[..., Awaitable[ConsumeResetCreditResponse]]
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


@router.get(
    "/{account_id}/rate-limit-reset-credits",
    response_model=RateLimitResetCreditsSnapshotResponse | None,
)
async def get_rate_limit_reset_credits(
    account_id: str,
) -> RateLimitResetCreditsSnapshotResponse | None:
    snapshot = get_rate_limit_reset_credits_store().get(account_id)
    return _snapshot_to_response(snapshot)


@router.post(
    "/{account_id}/rate-limit-reset-credits/consume",
    response_model=ConsumeResetCreditResponseSchema,
)
async def consume_rate_limit_reset_credit(
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> ConsumeResetCreditResponseSchema:
    account = await context.repository.get_by_id(account_id)
    if account is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return await _redeem_soonest_reset_credit(
        account=account,
        store=get_rate_limit_reset_credits_store(),
        encryptor=TokenEncryptor(),
        consume_fn=consume_reset_credit,
    )


async def _redeem_soonest_reset_credit(
    *,
    account: Account,
    store: RateLimitResetCreditsStore,
    encryptor: TokenEncryptor,
    consume_fn: ConsumeFn,
) -> ConsumeResetCreditResponseSchema:
    lock = await _get_redeem_lock(account.id)
    async with lock:
        snapshot = store.get(account.id)
        credit = _select_soonest_available_credit(snapshot)
        if credit is None:
            raise DashboardConflictError("No available reset credit", code="no_available_reset_credit")
        access_token = encryptor.decrypt(account.access_token_encrypted)
        try:
            result = await consume_fn(access_token, account.chatgpt_account_id, credit.id)
        except ConsumeResetCreditError as exc:
            raise _translate_consume_error(exc) from exc
        redeemed_at = result.credit.redeemed_at if result.credit else None
        await store.invalidate(account.id)
        return ConsumeResetCreditResponseSchema(
            code=result.code,
            windows_reset=result.windows_reset,
            redeemed_at=redeemed_at,
        )


async def _get_redeem_lock(account_id: str) -> asyncio.Lock:
    lock = _redeem_locks.get(account_id)
    if lock is not None:
        return lock
    async with _redeem_locks_registry_lock:
        lock = _redeem_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _redeem_locks[account_id] = lock
        return lock


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
    if snapshot.available_count <= 0:
        return None
    available = [credit for credit in snapshot.credits if credit.status == "available"]
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
