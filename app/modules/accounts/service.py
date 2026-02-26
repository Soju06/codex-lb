from __future__ import annotations

import json
from datetime import timedelta

from pydantic import ValidationError

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
)
from app.core.auth.anthropic_credentials import parse_anthropic_auth_json
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.mappers import build_account_summaries, build_account_usage_trends
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import (
    AccountImportResponse,
    AccountSummary,
    AccountTrendsResponse,
)
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

_SPARKLINE_DAYS = 7
_DETAIL_BUCKET_SECONDS = 3600  # 1h → 168 points


class InvalidAuthJsonError(Exception):
    pass


class InvalidAnthropicAuthJsonError(Exception):
    pass


class InvalidAnthropicEmailError(Exception):
    pass


class AccountsService:
    def __init__(
        self,
        repo: AccountsRepository,
        usage_repo: UsageRepository | None = None,
    ) -> None:
        self._repo = repo
        self._usage_repo = usage_repo
        self._usage_updater = UsageUpdater(usage_repo, repo) if usage_repo else None
        self._encryptor = TokenEncryptor()

    async def list_accounts(self) -> list[AccountSummary]:
        accounts = await self._repo.list_accounts()
        if not accounts:
            return []
        primary_usage = await self._usage_repo.latest_by_account(window="primary") if self._usage_repo else {}
        secondary_usage = await self._usage_repo.latest_by_account(window="secondary") if self._usage_repo else {}

        return build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            encryptor=self._encryptor,
        )

    async def get_account_trends(self, account_id: str) -> AccountTrendsResponse | None:
        account = await self._repo.get_by_id(account_id)
        if not account or not self._usage_repo:
            return None
        now = utcnow()
        since = now - timedelta(days=_SPARKLINE_DAYS)
        since_epoch = naive_utc_to_epoch(since)
        bucket_count = (_SPARKLINE_DAYS * 24 * 3600) // _DETAIL_BUCKET_SECONDS
        buckets = await self._usage_repo.trends_by_bucket(
            since=since,
            bucket_seconds=_DETAIL_BUCKET_SECONDS,
            account_id=account_id,
        )
        trends = build_account_usage_trends(buckets, since_epoch, _DETAIL_BUCKET_SECONDS, bucket_count)
        trend = trends.get(account_id)
        return AccountTrendsResponse(
            account_id=account_id,
            primary=trend.primary if trend else [],
            secondary=trend.secondary if trend else [],
        )

    async def import_account(self, raw: bytes) -> AccountImportResponse:
        try:
            auth = parse_auth_json(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError, TypeError) as exc:
            raise InvalidAuthJsonError("Invalid auth.json payload") from exc
        claims = claims_from_auth(auth)

        email = claims.email or DEFAULT_EMAIL
        raw_account_id = claims.account_id
        account_id = generate_unique_account_id(raw_account_id, email)
        plan_type = coerce_account_plan_type(claims.plan_type, DEFAULT_PLAN)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()

        account = Account(
            id=account_id,
            chatgpt_account_id=raw_account_id,
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

    async def import_anthropic_account(self, raw: bytes, *, email: str) -> AccountImportResponse:
        try:
            auth = parse_anthropic_auth_json(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise InvalidAnthropicAuthJsonError("Invalid Anthropic credential payload") from exc

        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise InvalidAnthropicEmailError("Invalid Anthropic account email")

        settings = get_settings()
        account_id = settings.anthropic_default_account_id
        plan_type = coerce_account_plan_type(settings.anthropic_default_plan_type, DEFAULT_PLAN)

        refresh_token = auth.refresh_token or ""
        encrypted_access = self._encryptor.encrypt(auth.access_token)
        encrypted_refresh = self._encryptor.encrypt(refresh_token)
        encrypted_id = self._encryptor.encrypt("")

        existing = await self._repo.get_by_id(account_id)
        if existing is None:
            account = Account(
                id=account_id,
                chatgpt_account_id=None,
                email=normalized_email,
                plan_type=plan_type,
                access_token_encrypted=encrypted_access,
                refresh_token_encrypted=encrypted_refresh,
                id_token_encrypted=encrypted_id,
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
            saved = await self._repo.upsert(account, merge_by_email=False)
        else:
            await self._repo.update_tokens(
                account_id=account_id,
                access_token_encrypted=encrypted_access,
                refresh_token_encrypted=encrypted_refresh,
                id_token_encrypted=encrypted_id,
                last_refresh=utcnow(),
                plan_type=plan_type,
                email=normalized_email,
            )
            await self._repo.update_status(account_id, AccountStatus.ACTIVE, None)
            saved = await self._repo.get_by_id(account_id)
            if saved is None:
                raise RuntimeError("Failed to load saved Anthropic account")
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
