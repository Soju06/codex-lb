from __future__ import annotations

import json
from datetime import timedelta

from pydantic import ValidationError

from app.core.auth import claims_from_auth, parse_auth_json
from app.core.clients.oauth import OAuthTokens
from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.credential_automator import CredentialAuthAutomator, CredentialAuthorizationError
from app.modules.accounts.credential_parser import parse_credential_lines
from app.modules.accounts.mappers import build_account_summaries, build_account_usage_trends
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import (
    AccountImportResponse,
    AccountSummary,
    AccountTrendsResponse,
    CredentialImportResult,
    CredentialsImportResponse,
)
from app.modules.accounts.token_factory import PersistableTokenPayload, account_from_token_payload
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

_SPARKLINE_DAYS = 7
_DETAIL_BUCKET_SECONDS = 3600  # 1h → 168 points


class InvalidAuthJsonError(Exception):
    pass


class AccountsService:
    def __init__(
        self,
        repo: AccountsRepository,
        usage_repo: UsageRepository | None = None,
        credential_automator: CredentialAuthAutomator | None = None,
    ) -> None:
        self._repo = repo
        self._usage_repo = usage_repo
        self._usage_updater = UsageUpdater(usage_repo, repo) if usage_repo else None
        self._encryptor = TokenEncryptor()
        self._credential_automator = credential_automator or CredentialAuthAutomator()

    async def list_accounts(self, *, owner_user_id: str | None = None) -> list[AccountSummary]:
        accounts = await self._repo.list_accounts(owner_user_id=owner_user_id)
        if not accounts:
            return []
        primary_usage = (
            await self._usage_repo.latest_by_account(window="primary", owner_user_id=owner_user_id)
            if self._usage_repo
            else {}
        )
        secondary_usage = (
            await self._usage_repo.latest_by_account(window="secondary", owner_user_id=owner_user_id)
            if self._usage_repo
            else {}
        )

        return build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            encryptor=self._encryptor,
        )

    async def get_account_trends(self, account_id: str, *, owner_user_id: str | None = None) -> AccountTrendsResponse | None:
        account = await self._repo.get_by_id(account_id, owner_user_id=owner_user_id)
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
            owner_user_id=owner_user_id,
        )
        trends = build_account_usage_trends(buckets, since_epoch, _DETAIL_BUCKET_SECONDS, bucket_count)
        trend = trends.get(account_id)
        return AccountTrendsResponse(
            account_id=account_id,
            primary=trend.primary if trend else [],
            secondary=trend.secondary if trend else [],
        )

    async def import_account(self, raw: bytes, *, owner_user_id: str) -> AccountImportResponse:
        try:
            auth = parse_auth_json(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError, TypeError) as exc:
            raise InvalidAuthJsonError("Invalid auth.json payload") from exc
        claims = claims_from_auth(auth)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()

        account = account_from_token_payload(
            PersistableTokenPayload(
                access_token=auth.tokens.access_token,
                refresh_token=auth.tokens.refresh_token,
                id_token=auth.tokens.id_token,
                account_id=claims.account_id,
                last_refresh=last_refresh,
            ),
            encryptor=self._encryptor,
        )
        saved = await self._save_account(account, owner_user_id=owner_user_id)
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def import_credentials(self, credentials_text: str, *, owner_user_id: str) -> CredentialsImportResponse:
        credential_lines = parse_credential_lines(credentials_text)
        results: list[CredentialImportResult] = []
        imported = 0

        for line in credential_lines:
            try:
                tokens = await self._credential_automator.authorize(
                    email=line.email,
                    password=line.account_password,
                    totp_secret=line.totp_secret,
                )
                saved = await self._save_oauth_tokens(tokens, owner_user_id=owner_user_id)
                imported += 1
                results.append(
                    CredentialImportResult(
                        line=line.line,
                        email=line.email,
                        status="imported",
                        account_id=saved.id,
                    ),
                )
            except CredentialAuthorizationError as exc:
                results.append(
                    CredentialImportResult(
                        line=line.line,
                        email=line.email,
                        status="failed",
                        error=str(exc),
                    ),
                )
            except Exception as exc:
                results.append(
                    CredentialImportResult(
                        line=line.line,
                        email=line.email,
                        status="failed",
                        error=str(exc),
                    ),
                )

        failed = len(results) - imported
        return CredentialsImportResponse(
            total=len(results),
            imported=imported,
            failed=failed,
            results=results,
        )

    async def reactivate_account(self, account_id: str, *, owner_user_id: str | None = None) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.ACTIVE, None, owner_user_id=owner_user_id)

    async def pause_account(self, account_id: str, *, owner_user_id: str | None = None) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.PAUSED, None, owner_user_id=owner_user_id)

    async def delete_account(self, account_id: str, *, owner_user_id: str | None = None) -> bool:
        return await self._repo.delete(account_id, owner_user_id=owner_user_id)

    async def _save_oauth_tokens(self, tokens: OAuthTokens, *, owner_user_id: str) -> Account:
        account = account_from_token_payload(
            PersistableTokenPayload(
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                id_token=tokens.id_token,
                last_refresh=utcnow(),
            ),
            encryptor=self._encryptor,
        )
        return await self._save_account(account, owner_user_id=owner_user_id)

    async def _save_account(self, account: Account, *, owner_user_id: str) -> Account:
        saved = await self._repo.upsert(account, owner_user_id=owner_user_id)
        if self._usage_repo and self._usage_updater:
            latest_usage = await self._usage_repo.latest_by_account(window="primary", owner_user_id=owner_user_id)
            await self._usage_updater.refresh_accounts([saved], latest_usage)
        return saved
