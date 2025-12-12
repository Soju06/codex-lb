from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core import usage as usage_core
from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    extract_id_token_claims,
    fallback_account_id,
    parse_auth_json,
)

from app.core.crypto import TokenEncryptor
from app.core.usage.logs import cost_from_log
from app.core.utils.time import from_epoch_seconds, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import (
    AccountAuthStatus,
    AccountImportResponse,
    AccountSummary,
    AccountTokenStatus,
    AccountUsage,
)
from app.modules.proxy.usage_updater import UsageUpdater
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository


class AccountsService:
    def __init__(
        self,
        repo: AccountsRepository,
        usage_repo: UsageRepository | None = None,
        logs_repo: RequestLogsRepository | None = None,
    ) -> None:
        self._repo = repo
        self._usage_repo = usage_repo
        self._logs_repo = logs_repo
        self._usage_updater = UsageUpdater(usage_repo, repo) if usage_repo else None
        self._encryptor = TokenEncryptor()

    async def list_accounts(self) -> list[AccountSummary]:
        accounts = await self._repo.list_accounts()
        if not accounts:
            return []
        await self._refresh_usage(accounts)
        primary_usage = (
            await self._usage_repo.latest_by_account(window="primary")
            if self._usage_repo
            else {}
        )
        secondary_usage = (
            await self._usage_repo.latest_by_account(window="secondary")
            if self._usage_repo
            else {}
        )
        cost_by_account = await self._costs_last_24h()
        return [
            self._account_to_summary(
                account,
                primary_usage.get(account.id),
                secondary_usage.get(account.id),
                cost_by_account.get(account.id),
            )
            for account in accounts
        ]

    async def import_account(self, raw: bytes) -> AccountImportResponse:
        auth = parse_auth_json(raw)
        claims = claims_from_auth(auth)

        email = claims.email or DEFAULT_EMAIL
        plan_type = claims.plan_type or DEFAULT_PLAN
        account_id = claims.account_id or fallback_account_id(email)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()

        account = Account(
            id=account_id,
            email=email,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(auth.tokens.access_token),
            refresh_token_encrypted=self._encryptor.encrypt(auth.tokens.refresh_token),
            id_token_encrypted=self._encryptor.encrypt(auth.tokens.id_token),
            last_refresh=last_refresh,
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )

        saved = await self._repo.upsert(account)
        if self._usage_repo and self._usage_updater:
            latest_usage = await self._usage_repo.latest_by_account(window="primary")
            await self._usage_updater.refresh_accounts([saved], latest_usage)
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def reactivate_account(self, account_id: str) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.ACTIVE, None)

    async def pause_account(self, account_id: str) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.PAUSED, None)

    async def delete_account(self, account_id: str) -> bool:
        return await self._repo.delete(account_id)

    def _account_to_summary(
        self,
        account: Account,
        primary_usage: UsageHistory | None,
        secondary_usage: UsageHistory | None,
        cost_usd_24h: float | None,
    ) -> AccountSummary:
        auth_status = self._build_auth_status(account)
        primary_used_percent = _normalize_used_percent(primary_usage) or 0.0
        secondary_used_percent = _normalize_used_percent(secondary_usage) or 0.0
        primary_remaining_percent = usage_core.remaining_percent_from_used(primary_used_percent) or 0.0
        secondary_remaining_percent = usage_core.remaining_percent_from_used(secondary_used_percent) or 0.0
        reset_at_primary = (
            from_epoch_seconds(primary_usage.reset_at) if primary_usage is not None else None
        )
        reset_at_secondary = (
            from_epoch_seconds(secondary_usage.reset_at) if secondary_usage is not None else None
        )
        capacity_primary = usage_core.capacity_for_plan(account.plan_type, "primary")
        capacity_secondary = usage_core.capacity_for_plan(account.plan_type, "secondary")
        remaining_credits_primary = usage_core.remaining_credits_from_percent(
            primary_used_percent,
            capacity_primary,
        )
        remaining_credits_secondary = usage_core.remaining_credits_from_percent(
            secondary_used_percent,
            capacity_secondary,
        )
        return AccountSummary(
            account_id=account.id,
            email=account.email,
            display_name=account.email,
            plan_type=account.plan_type,
            status=account.status.value,
            usage=AccountUsage(
                primary_remaining_percent=primary_remaining_percent,
                secondary_remaining_percent=secondary_remaining_percent,
            ),
            reset_at_primary=reset_at_primary,
            reset_at_secondary=reset_at_secondary,
            last_refresh_at=account.last_refresh,
            capacity_credits_primary=capacity_primary,
            remaining_credits_primary=remaining_credits_primary,
            capacity_credits_secondary=capacity_secondary,
            remaining_credits_secondary=remaining_credits_secondary,
            cost_usd_24h=cost_usd_24h if cost_usd_24h is not None else 0.0,
            deactivation_reason=account.deactivation_reason,
            auth=auth_status,
        )

    def _build_auth_status(self, account: Account) -> AccountAuthStatus:
        access_token = self._decrypt_token(account.access_token_encrypted)
        refresh_token = self._decrypt_token(account.refresh_token_encrypted)
        id_token = self._decrypt_token(account.id_token_encrypted)

        access_expires = _token_expiry(access_token)
        refresh_state = "stored" if refresh_token else "missing"
        id_state = "unknown"
        if id_token:
            claims = extract_id_token_claims(id_token)
            if claims.model_dump(exclude_none=True):
                id_state = "parsed"

        return AccountAuthStatus(
            access=AccountTokenStatus(expires_at=access_expires),
            refresh=AccountTokenStatus(state=refresh_state),
            id_token=AccountTokenStatus(state=id_state),
        )

    def _decrypt_token(self, encrypted: bytes | None) -> str | None:
        if not encrypted:
            return None
        try:
            return self._encryptor.decrypt(encrypted)
        except Exception:
            return None

    async def _refresh_usage(self, accounts: list[Account]) -> None:
        if not self._usage_repo or not self._usage_updater:
            return
        latest_usage = await self._usage_repo.latest_by_account(window="primary")
        await self._usage_updater.refresh_accounts(accounts, latest_usage)

    async def _costs_last_24h(self) -> dict[str, float]:
        if not self._logs_repo:
            return {}
        since = utcnow() - timedelta(hours=24)
        logs = await self._logs_repo.list_since(since)
        totals: dict[str, float] = {}
        for log in logs:
            cost = cost_from_log(log)
            if cost is None:
                continue
            totals[log.account_id] = totals.get(log.account_id, 0.0) + cost
        return {account_id: round(total, 6) for account_id, total in totals.items()}


def _token_expiry(token: str | None) -> datetime | None:
    if not token:
        return None
    claims = extract_id_token_claims(token)
    exp = claims.exp
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    if isinstance(exp, str) and exp.isdigit():
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    return None


def _normalize_used_percent(entry: UsageHistory | None) -> float | None:
    if not entry:
        return None
    return entry.used_percent
