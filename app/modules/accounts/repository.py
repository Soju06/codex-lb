from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog, StickySession, UsageHistory

_SETTINGS_ROW_ID = 1
_DUPLICATE_ACCOUNT_SUFFIX = "__copy"
_UNSET = object()
_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"


@dataclass(frozen=True, slots=True)
class AccountRequestUsageSummary:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


@dataclass(frozen=True, slots=True)
class _RotatedTokens:
    """OAuth tokens rotated by a proxy probe, passed to :meth:`update_proxy`."""

    access_token_encrypted: bytes
    refresh_token_encrypted: bytes
    id_token_encrypted: bytes
    last_refresh: datetime


@dataclass(frozen=True, slots=True)
class AccountProxyRecord:
    """Snapshot of an account's stored SOCKS5 proxy configuration."""

    host: str
    port: int
    username: str | None
    password_encrypted: bytes | None
    remote_dns: bool
    label: str | None
    last_validated_at: datetime | None


class AccountIdentityConflictError(Exception):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(
            f"Cannot overwrite account for email '{email}' because multiple matching accounts exist. "
            "Remove duplicates or enable import without overwrite."
        )


class AccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        stmt = select(Account).order_by(Account.email)
        if refresh_existing:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_request_usage_summary_by_account(
        self,
        account_ids: list[str] | None = None,
    ) -> dict[str, AccountRequestUsageSummary]:
        summaries: dict[str, AccountRequestUsageSummary] = {}
        output_tokens_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
        stmt = (
            select(
                RequestLog.account_id,
                func.count(RequestLog.id).label("request_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
            )
            .where((RequestLog.source.is_(None)) | (RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE))
            .group_by(RequestLog.account_id)
        )
        if account_ids:
            stmt = stmt.where(RequestLog.account_id.in_(account_ids))

        result = await self._session.execute(stmt)
        for (
            account_id,
            request_count,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            total_cost_usd,
        ) in result.all():
            if not account_id:
                continue
            input_sum = int(input_tokens or 0)
            output_sum = int(output_tokens or 0)
            cached_sum = int(cached_input_tokens or 0)
            cached_sum = max(0, min(cached_sum, input_sum))
            return_row = AccountRequestUsageSummary(
                request_count=int(request_count or 0),
                total_tokens=input_sum + output_sum,
                cached_input_tokens=cached_sum,
                total_cost_usd=round(float(total_cost_usd or 0.0), 6),
            )
            summaries[account_id] = return_row

        return summaries

    async def exists_active_chatgpt_account_id(self, chatgpt_account_id: str) -> bool:
        return await self.get_active_account_id_by_chatgpt_account_id(chatgpt_account_id) is not None

    async def list_active_account_ids_by_chatgpt_account_id(
        self,
        chatgpt_account_id: str,
        *,
        limit: int = 100,
    ) -> list[str]:
        result = await self._session.execute(
            select(Account.id)
            .where(Account.chatgpt_account_id == chatgpt_account_id)
            .where(Account.status.notin_((AccountStatus.DEACTIVATED, AccountStatus.PAUSED)))
            .order_by(Account.created_at.asc(), Account.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_active_account_id_by_chatgpt_account_id(self, chatgpt_account_id: str) -> str | None:
        matches = await self.list_active_account_ids_by_chatgpt_account_id(chatgpt_account_id, limit=1)
        return matches[0] if matches else None

    async def upsert(
        self,
        account: Account,
        *,
        merge_by_email: bool | None = None,
        include_proxy_fields: bool = False,
    ) -> Account:
        dialect_name = self._dialect_name()
        sqlite_lock_acquired = False
        if merge_by_email is None:
            if dialect_name == "sqlite":
                await self._acquire_sqlite_merge_lock()
                sqlite_lock_acquired = True
            merge_by_email = await self._merge_by_email_enabled()

        if merge_by_email:
            if dialect_name == "sqlite" and not sqlite_lock_acquired:
                await self._acquire_sqlite_merge_lock()
            elif dialect_name == "postgresql":
                await self._acquire_postgresql_merge_lock(account.email)
        else:
            if dialect_name == "sqlite" and not sqlite_lock_acquired:
                await self._acquire_sqlite_merge_lock()
            elif dialect_name == "postgresql":
                await self._acquire_postgresql_identity_lock(account.id)

        existing = await self._session.get(Account, account.id)
        if existing:
            if merge_by_email:
                _apply_account_updates(existing, account, include_proxy_fields=include_proxy_fields)
                await self._session.commit()
                await self._session.refresh(existing)
                return existing
            account.id = await self._next_available_account_id(account.id)

        if merge_by_email:
            existing_by_email = await self._single_account_by_email(account.email)
            if existing_by_email:
                _apply_account_updates(
                    existing_by_email,
                    account,
                    include_proxy_fields=include_proxy_fields,
                )
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def preview_import_account_id(
        self,
        account_id: str,
        email: str,
        *,
        merge_by_email: bool | None = None,
    ) -> str:
        """Return the account id ``upsert`` will normally use for an import.

        Import-time proxy validation needs to know which row will receive the
        proxy fields and rotated tokens before the account is persisted. This
        is intentionally a preview, not a reservation: ``upsert`` still
        enforces the final identity rules under its normal database locks.
        """

        if merge_by_email is None:
            merge_by_email = await self._merge_by_email_enabled()
        existing = await self._session.get(Account, account_id)
        if existing is not None:
            if merge_by_email:
                return existing.id
            return await self._next_available_account_id(account_id)
        if merge_by_email:
            existing_by_email = await self._single_account_by_email(email)
            if existing_by_email is not None:
                return existing_by_email.id
            return account_id
        return account_id

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
    ) -> bool:
        values: dict[str, object | None] = {
            "status": status,
            "deactivation_reason": deactivation_reason,
            "reset_at": reset_at,
        }
        if blocked_at is not _UNSET:
            values["blocked_at"] = blocked_at
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None | object = _UNSET,
    ) -> bool:
        values: dict[str, object | None] = {
            "status": status,
            "deactivation_reason": deactivation_reason,
            "reset_at": reset_at,
        }
        if blocked_at is not _UNSET:
            values["blocked_at"] = blocked_at
        stmt = (
            update(Account)
            .where(Account.id == account_id)
            .where(Account.status == expected_status)
            .values(**values)
            .returning(Account.id)
        )
        if expected_deactivation_reason is None:
            stmt = stmt.where(Account.deactivation_reason.is_(None))
        else:
            stmt = stmt.where(Account.deactivation_reason == expected_deactivation_reason)
        if expected_reset_at is None:
            stmt = stmt.where(Account.reset_at.is_(None))
        else:
            stmt = stmt.where(Account.reset_at == expected_reset_at)
        if expected_blocked_at is not _UNSET:
            if expected_blocked_at is None:
                stmt = stmt.where(Account.blocked_at.is_(None))
            else:
                stmt = stmt.where(Account.blocked_at == expected_blocked_at)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_alias(self, account_id: str, alias: str | None) -> bool:
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(alias=alias).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_limit_warmup_enabled(self, account_id: str, enabled: bool) -> bool:
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(limit_warmup_enabled=enabled).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def delete(self, account_id: str, *, delete_history: bool = False) -> bool:
        await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
        if delete_history:
            await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
        else:
            await self._session.execute(
                update(RequestLog)
                .where(RequestLog.account_id == account_id)
                .values(account_id=None, deleted_at=utcnow()),
            )
        await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
        result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
    ) -> bool:
        values: dict[str, bytes | datetime | str] = {
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "id_token_encrypted": id_token_encrypted,
            "last_refresh": last_refresh,
        }
        if plan_type is not None:
            values["plan_type"] = plan_type
        if email is not None:
            values["email"] = email
        if chatgpt_account_id is not None:
            values["chatgpt_account_id"] = chatgpt_account_id
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def get_proxy_config(self, account_id: str) -> AccountProxyRecord | None:
        """Return the stored proxy configuration for an account, or ``None``.

        ``None`` is returned both for unknown accounts and for accounts that
        do not have a proxy configured (``proxy_host IS NULL``). Callers that
        need to differentiate the two should ``get_by_id`` separately.
        """

        result = await self._session.execute(
            select(
                Account.proxy_host,
                Account.proxy_port,
                Account.proxy_username,
                Account.proxy_password_encrypted,
                Account.proxy_remote_dns,
                Account.proxy_label,
                Account.proxy_last_validated_at,
            ).where(Account.id == account_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        (
            host,
            port,
            username,
            password_encrypted,
            remote_dns,
            label,
            last_validated_at,
        ) = row
        if host is None or port is None:
            return None
        return AccountProxyRecord(
            host=host,
            port=int(port),
            username=username,
            password_encrypted=bytes(password_encrypted) if password_encrypted is not None else None,
            remote_dns=bool(remote_dns) if remote_dns is not None else True,
            label=label,
            last_validated_at=last_validated_at,
        )

    async def update_proxy(
        self,
        account_id: str,
        *,
        host: str,
        port: int,
        username: str | None,
        password_encrypted: bytes | None,
        remote_dns: bool,
        label: str | None,
        last_validated_at: datetime | None,
        rotated_tokens: _RotatedTokens | None = None,
    ) -> bool:
        """Persist a complete proxy configuration on an account.

        The repository layer is encryption-agnostic; callers MUST pass an
        already-encrypted ``password_encrypted`` value (typically produced
        via ``TokenEncryptor.encrypt``). All proxy columns are written in a
        single statement so partial-update races cannot leave the row in a
        torn state.

        When ``rotated_tokens`` is provided the token columns are written
        in the same statement so proxy config and credential rotation are
        atomic.
        """

        if not host:
            raise ValueError("proxy host must be a non-empty string")
        if not (1 <= int(port) <= 65535):
            raise ValueError("proxy port must be in range 1-65535")

        values: dict[str, object | None] = {
            "proxy_host": host,
            "proxy_port": int(port),
            "proxy_username": username,
            "proxy_password_encrypted": password_encrypted,
            "proxy_remote_dns": bool(remote_dns),
            "proxy_label": label,
            "proxy_last_validated_at": last_validated_at,
        }
        if rotated_tokens is not None:
            values["access_token_encrypted"] = rotated_tokens.access_token_encrypted
            values["refresh_token_encrypted"] = rotated_tokens.refresh_token_encrypted
            values["id_token_encrypted"] = rotated_tokens.id_token_encrypted
            values["last_refresh"] = rotated_tokens.last_refresh
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def clear_proxy(self, account_id: str) -> bool:
        """Clear the SOCKS5 proxy fields on an account.

        Resets every proxy column to its no-proxy default: NULL for the
        connection fields, ``true`` for ``proxy_remote_dns``, NULL for
        ``proxy_last_validated_at``.

        Account TLS behavior is fixed to the internal Codex profile.
        """

        values: dict[str, object | None] = {
            "proxy_host": None,
            "proxy_port": None,
            "proxy_username": None,
            "proxy_password_encrypted": None,
            "proxy_remote_dns": True,
            "proxy_label": None,
            "proxy_last_validated_at": None,
        }
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def _merge_by_email_enabled(self) -> bool:
        settings = await self._session.get(DashboardSettings, _SETTINGS_ROW_ID)
        if settings is None:
            return True
        return not settings.import_without_overwrite

    async def _next_available_account_id(self, base_id: str) -> str:
        candidate = base_id
        sequence = 2
        while await self._session.get(Account, candidate) is not None:
            candidate = f"{base_id}{_DUPLICATE_ACCOUNT_SUFFIX}{sequence}"
            sequence += 1
        return candidate

    async def _single_account_by_email(self, email: str) -> Account | None:
        result = await self._session.execute(
            select(Account).where(Account.email == email).order_by(Account.created_at.asc(), Account.id.asc()).limit(2)
        )
        matches = list(result.scalars().all())
        if not matches:
            return None
        if len(matches) > 1:
            raise AccountIdentityConflictError(email)
        return matches[0]

    def _dialect_name(self) -> str:
        return self._session.get_bind().dialect.name

    async def _acquire_sqlite_merge_lock(self) -> None:
        try:
            await self._session.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as exc:
            message = str(exc).lower()
            if "within a transaction" not in message:
                raise
            # A no-op write escalates the current deferred transaction to a write
            # transaction, serializing concurrent writers.
            await self._session.execute(text("UPDATE accounts SET id = id WHERE 1 = 0"))

    async def _acquire_postgresql_merge_lock(self, email: str) -> None:
        lock_key = _advisory_lock_key("merge-email", email)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    async def _acquire_postgresql_identity_lock(self, account_id: str) -> None:
        lock_key = _advisory_lock_key("account-id", account_id)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )


def _apply_account_updates(
    target: Account,
    source: Account,
    *,
    include_proxy_fields: bool = False,
) -> None:
    target.chatgpt_account_id = source.chatgpt_account_id
    target.email = source.email
    target.plan_type = source.plan_type
    target.access_token_encrypted = source.access_token_encrypted
    target.refresh_token_encrypted = source.refresh_token_encrypted
    target.id_token_encrypted = source.id_token_encrypted
    target.last_refresh = source.last_refresh
    target.status = source.status
    target.deactivation_reason = source.deactivation_reason
    target.reset_at = source.reset_at
    target.blocked_at = source.blocked_at
    if include_proxy_fields:
        target.proxy_host = source.proxy_host
        target.proxy_port = source.proxy_port
        target.proxy_username = source.proxy_username
        target.proxy_password_encrypted = source.proxy_password_encrypted
        target.proxy_remote_dns = source.proxy_remote_dns
        target.proxy_label = source.proxy_label
        target.proxy_last_validated_at = source.proxy_last_validated_at


def _advisory_lock_key(scope: str, value: str) -> int:
    digest = hashlib.sha256(f"{scope}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
