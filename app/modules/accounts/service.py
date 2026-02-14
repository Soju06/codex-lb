from __future__ import annotations

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
)
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.mappers import build_account_summaries
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import (
    AccountImportResponse,
    AccountSummary,
)
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater


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
        spark_primary_usage = (
            await self._usage_repo.latest_by_account(window="spark_primary") if self._usage_repo else {}
        )
        spark_secondary_usage = (
            await self._usage_repo.latest_by_account(window="spark_secondary") if self._usage_repo else {}
        )
        return build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            spark_primary_usage=spark_primary_usage,
            spark_secondary_usage=spark_secondary_usage,
            encryptor=self._encryptor,
        )

    async def import_account(self, raw: bytes) -> AccountImportResponse:
        auth = parse_auth_json(raw)
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

    async def reactivate_account(self, account_id: str) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.ACTIVE, None)

    async def pause_account(self, account_id: str) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.PAUSED, None)

    async def delete_account(self, account_id: str) -> bool:
        return await self._repo.delete(account_id)
