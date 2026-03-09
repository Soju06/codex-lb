from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import timedelta

from pydantic import ValidationError

from app.core.auth import (
    AuthFile,
    AuthTokens,
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
    token_expiry,
)
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.mappers import build_account_summaries, build_account_usage_trends
from app.modules.accounts.repository import AccountIdentityConflictError, AccountsRepository
from app.modules.accounts.schemas import (
    AccountImportBatchResponse,
    AccountImportFailure,
    AccountImportResponse,
    AccountRequestUsage,
    AccountSummary,
    AccountTrendsResponse,
)
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater
from app.core.auth.refresh import RefreshError, TokenRefreshResult, refresh_access_token

_SPARKLINE_DAYS = 7
_DETAIL_BUCKET_SECONDS = 3600  # 1h → 168 points


class InvalidAuthJsonError(Exception):
    pass


class AuthRefreshFailedError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ImportFilePayload:
    filename: str | None
    raw: bytes


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
        self._auth_manager = AuthManager(repo)

    async def list_accounts(self) -> list[AccountSummary]:
        accounts = await self._repo.list_accounts()
        if not accounts:
            return []
        primary_usage = await self._usage_repo.latest_by_account(window="primary") if self._usage_repo else {}
        secondary_usage = await self._usage_repo.latest_by_account(window="secondary") if self._usage_repo else {}
        request_usage_rows = await self._repo.list_request_usage_summary_by_account(
            [account.id for account in accounts]
        )
        request_usage_by_account = {
            account_id: AccountRequestUsage(
                request_count=row.request_count,
                total_tokens=row.total_tokens,
                cached_input_tokens=row.cached_input_tokens,
                total_cost_usd=row.total_cost_usd,
            )
            for account_id, row in request_usage_rows.items()
        }

        return build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            request_usage_by_account=request_usage_by_account,
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

    async def import_account(self, raw: bytes, *, filename: str | None = None) -> AccountImportResponse:
        try:
            auth = parse_auth_json(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError, TypeError) as exc:
            raise InvalidAuthJsonError("Invalid auth.json payload") from exc
        auth, refreshed_on_import = await self._refresh_import_auth_if_needed(auth)
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
            filename=filename,
            account_id=saved.id,
            email=saved.email,
            plan_type=saved.plan_type,
            status=saved.status,
            refreshed_on_import=refreshed_on_import,
        )

    async def import_accounts(self, files: list[ImportFilePayload]) -> AccountImportBatchResponse:
        imported: list[AccountImportResponse] = []
        failed: list[AccountImportFailure] = []

        for file in files:
            try:
                imported.append(await self.import_account(file.raw, filename=file.filename))
            except InvalidAuthJsonError as exc:
                failed.append(
                    AccountImportFailure(filename=file.filename, code="invalid_auth_json", message=str(exc)),
                )
            except AccountIdentityConflictError as exc:
                failed.append(
                    AccountImportFailure(
                        filename=file.filename,
                        code="duplicate_identity_conflict",
                        message=str(exc),
                    ),
                )
            except AuthRefreshFailedError as exc:
                failed.append(
                    AccountImportFailure(filename=file.filename, code=exc.code, message=exc.message),
                )

        return AccountImportBatchResponse(imported=imported, failed=failed)

    async def export_accounts_archive(self) -> tuple[str, bytes]:
        accounts = await self._repo.list_accounts()
        archive_buffer = io.BytesIO()

        with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for account in accounts:
                export_account = await self._refresh_account_for_export(account)
                payload = self._serialize_account_auth(export_account)
                archive.writestr(
                    f"{_safe_archive_segment(export_account.email)}__{export_account.id}/auth.json",
                    json.dumps(payload, indent=2),
                )

        filename = f"auth-export-{utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
        return filename, archive_buffer.getvalue()

    async def reactivate_account(self, account_id: str) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.ACTIVE, None)

    async def pause_account(self, account_id: str) -> bool:
        return await self._repo.update_status(account_id, AccountStatus.PAUSED, None)

    async def delete_account(self, account_id: str) -> bool:
        return await self._repo.delete(account_id)

    async def _refresh_import_auth_if_needed(self, auth: AuthFile) -> tuple[AuthFile, bool]:
        expires_at = token_expiry(auth.tokens.access_token)
        if not expires_at or to_utc_naive(expires_at) > utcnow() or not auth.tokens.refresh_token:
            return auth, False

        try:
            result = await refresh_access_token(auth.tokens.refresh_token)
        except RefreshError as exc:
            raise AuthRefreshFailedError("refresh_failed", exc.message) from exc
        return _auth_file_from_refresh_result(result), True

    async def _refresh_account_for_export(self, account: Account) -> Account:
        access_token = self._decrypt_token(account.access_token_encrypted)
        expires_at = token_expiry(access_token)
        should_force = expires_at is not None and to_utc_naive(expires_at) <= utcnow()

        try:
            if should_force:
                return await self._auth_manager.ensure_fresh(account, force=True)
            return await self._auth_manager.ensure_fresh(account)
        except RefreshError:
            return account

    def _serialize_account_auth(self, account: Account) -> dict[str, object]:
        access_token = self._decrypt_token(account.access_token_encrypted)
        refresh_token = self._decrypt_token(account.refresh_token_encrypted)
        id_token = self._decrypt_token(account.id_token_encrypted)
        payload = AuthFile(
            tokens=AuthTokens(
                idToken=id_token or "",
                accessToken=access_token or "",
                refreshToken=refresh_token or "",
                accountId=account.chatgpt_account_id,
            ),
            lastRefreshAt=account.last_refresh,
        )
        return payload.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _decrypt_token(self, encrypted: bytes | None) -> str | None:
        if not encrypted:
            return None
        try:
            return self._encryptor.decrypt(encrypted)
        except Exception:
            return None


def _auth_file_from_refresh_result(result: TokenRefreshResult) -> AuthFile:
    return AuthFile(
        tokens=AuthTokens(
            idToken=result.id_token,
            accessToken=result.access_token,
            refreshToken=result.refresh_token,
            accountId=result.account_id,
        ),
        lastRefreshAt=utcnow(),
    )


def _safe_archive_segment(value: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value.strip())
    return sanitized or "account"
