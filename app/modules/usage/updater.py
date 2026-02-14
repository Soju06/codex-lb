from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from app.core.auth.refresh import RefreshError
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.usage.models import RateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.auth_manager import AccountsRepositoryPort, AuthManager

logger = logging.getLogger(__name__)


class UsageRepositoryPort(Protocol):
    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        window_label: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
    ) -> UsageHistory | None: ...


class UsageUpdater:
    def __init__(
        self,
        usage_repo: UsageRepositoryPort,
        accounts_repo: AccountsRepositoryPort | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._encryptor = TokenEncryptor()
        self._auth_manager = AuthManager(accounts_repo) if accounts_repo else None

    async def refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: Mapping[str, UsageHistory],
    ) -> None:
        settings = get_settings()
        if not settings.usage_refresh_enabled:
            return

        now = utcnow()
        interval = settings.usage_refresh_interval_seconds
        for account in accounts:
            if account.status == AccountStatus.DEACTIVATED:
                continue
            latest = latest_usage.get(account.id)
            if latest and (now - latest.recorded_at).total_seconds() < interval:
                continue
            # NOTE: AsyncSession is not safe for concurrent use. Run sequentially
            # within the request-scoped session to avoid PK collisions and
            # flush-time warnings (SAWarning: Session.add during flush).
            try:
                await self._refresh_account(account, usage_account_id=account.chatgpt_account_id)
            except Exception as exc:
                logger.warning(
                    "Usage refresh failed account_id=%s request_id=%s error=%s",
                    account.id,
                    get_request_id(),
                    exc,
                    exc_info=True,
                )
                # swallow per-account failures so the whole refresh loop keeps going
                continue

    async def _refresh_account(self, account: Account, *, usage_account_id: str | None) -> None:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        payload: UsagePayload | None = None
        try:
            payload = await fetch_usage(
                access_token=access_token,
                account_id=usage_account_id,
            )
        except UsageFetchError as exc:
            if _should_deactivate_for_usage_error(exc.status_code):
                await self._deactivate_for_client_error(account, exc)
                return
            if exc.status_code != 401 or not self._auth_manager:
                return
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError:
                return
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            try:
                payload = await fetch_usage(
                    access_token=access_token,
                    account_id=usage_account_id,
                )
            except UsageFetchError as retry_exc:
                if _should_deactivate_for_usage_error(retry_exc.status_code):
                    await self._deactivate_for_client_error(account, retry_exc)
                return

        if payload is None:
            return

        rate_limit = payload.rate_limit
        if rate_limit is None:
            return

        primary = rate_limit.primary_window
        secondary = rate_limit.secondary_window
        credits_has, credits_unlimited, credits_balance = _credits_snapshot(payload)
        now_epoch = _now_epoch()

        if primary and primary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(primary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="primary",
                reset_at=_reset_at(primary.reset_at, primary.reset_after_seconds, now_epoch),
                window_minutes=_window_minutes(primary.limit_window_seconds),
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )

        if secondary and secondary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(secondary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="secondary",
                reset_at=_reset_at(secondary.reset_at, secondary.reset_after_seconds, now_epoch),
                window_minutes=_window_minutes(secondary.limit_window_seconds),
            )

        spark_windows = _extract_spark_windows(payload)
        if spark_windows.primary and spark_windows.primary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(spark_windows.primary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="spark_primary",
                window_label=spark_windows.window_label,
                reset_at=_reset_at(
                    spark_windows.primary.reset_at,
                    spark_windows.primary.reset_after_seconds,
                    now_epoch,
                ),
                window_minutes=_window_minutes(spark_windows.primary.limit_window_seconds),
            )

        if spark_windows.secondary and spark_windows.secondary.used_percent is not None:
            await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(spark_windows.secondary.used_percent),
                input_tokens=None,
                output_tokens=None,
                window="spark_secondary",
                window_label=spark_windows.window_label,
                reset_at=_reset_at(
                    spark_windows.secondary.reset_at,
                    spark_windows.secondary.reset_after_seconds,
                    now_epoch,
                ),
                window_minutes=_window_minutes(spark_windows.secondary.limit_window_seconds),
            )

    async def _deactivate_for_client_error(self, account: Account, exc: UsageFetchError) -> None:
        if not self._auth_manager:
            return
        reason = f"Usage API error: HTTP {exc.status_code} - {exc.message}"
        logger.warning(
            "Deactivating account due to client error account_id=%s status=%s message=%s request_id=%s",
            account.id,
            exc.status_code,
            exc.message,
            get_request_id(),
        )
        await self._auth_manager._repo.update_status(account.id, AccountStatus.DEACTIVATED, reason)
        account.status = AccountStatus.DEACTIVATED
        account.deactivation_reason = reason


def _credits_snapshot(payload: UsagePayload) -> tuple[bool | None, bool | None, float | None]:
    credits = payload.credits
    if credits is None:
        return None, None, None
    credits_has = credits.has_credits
    credits_unlimited = credits.unlimited
    balance_value = credits.balance
    return credits_has, credits_unlimited, _parse_credits_balance(balance_value)


def _parse_credits_balance(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _window_minutes(limit_seconds: int | None) -> int | None:
    if not limit_seconds or limit_seconds <= 0:
        return None
    return max(1, math.ceil(limit_seconds / 60))


def _now_epoch() -> int:
    return int(utcnow().replace(tzinfo=timezone.utc).timestamp())


def _reset_at(reset_at: int | None, reset_after_seconds: int | None, now_epoch: int) -> int | None:
    if reset_at is not None:
        return int(reset_at)
    if reset_after_seconds is None:
        return None
    return now_epoch + max(0, int(reset_after_seconds))


_DEACTIVATING_USAGE_STATUS_CODES = {402, 403, 404}


def _should_deactivate_for_usage_error(status_code: int) -> bool:
    return status_code in _DEACTIVATING_USAGE_STATUS_CODES


@dataclass(frozen=True, slots=True)
class UsageWindowPayload:
    used_percent: float | None
    reset_at: int | None
    limit_window_seconds: int | None
    reset_after_seconds: int | None


@dataclass(frozen=True, slots=True)
class SparkWindows:
    primary: UsageWindowPayload | None
    secondary: UsageWindowPayload | None
    window_label: str | None


def _extract_spark_windows(payload: UsagePayload) -> SparkWindows:
    rate_limit = payload.rate_limit
    if rate_limit is None:
        return SparkWindows(primary=None, secondary=None, window_label=None)

    inline_primary, inline_secondary, inline_label = _extract_inline_spark_windows(rate_limit)
    if inline_primary is not None or inline_secondary is not None:
        return SparkWindows(
            primary=inline_primary,
            secondary=inline_secondary,
            window_label=inline_label,
        )

    additional_limits = payload.additional_rate_limits or []
    for limit in additional_limits:
        limit_name = (limit.limit_name or "").strip()
        if "spark" not in limit_name.lower():
            continue
        spark_rate_limit = limit.rate_limit
        if spark_rate_limit is None:
            continue
        return SparkWindows(
            primary=_to_window_payload(spark_rate_limit.primary_window),
            secondary=_to_window_payload(spark_rate_limit.secondary_window),
            window_label=limit.limit_name,
        )

    return SparkWindows(primary=None, secondary=None, window_label=None)


def _extract_inline_spark_windows(
    rate_limit: RateLimitPayload,
) -> tuple[UsageWindowPayload | None, UsageWindowPayload | None, str | None]:
    spark_windows = [(key, _to_window_payload(window)) for key, window in rate_limit.spark_windows()]
    spark_windows = [(key, window) for key, window in spark_windows if window is not None]
    if not spark_windows:
        return None, None, None

    primary: UsageWindowPayload | None = None
    secondary: UsageWindowPayload | None = None
    fallback: list[UsageWindowPayload] = []
    for key, window in spark_windows:
        key_lower = key.lower()
        if primary is None and _is_primary_hint(key_lower):
            primary = window
            continue
        if secondary is None and _is_secondary_hint(key_lower):
            secondary = window
            continue
        fallback.append(window)

    for window in fallback:
        if primary is None:
            primary = window
            continue
        if secondary is None:
            secondary = window
            continue
        break

    return primary, secondary, spark_windows[0][0]


def _to_window_payload(window: UsageWindow | None) -> UsageWindowPayload | None:
    if window is None:
        return None
    return UsageWindowPayload(
        used_percent=_float_or_none(window.used_percent),
        reset_at=_int_or_none(window.reset_at),
        limit_window_seconds=_int_or_none(window.limit_window_seconds),
        reset_after_seconds=_int_or_none(window.reset_after_seconds),
    )


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _is_primary_hint(value: str) -> bool:
    return bool(re.search(r"(primary|5h|hour)", value))


def _is_secondary_hint(value: str) -> bool:
    return bool(re.search(r"(secondary|7d|week)", value))
