from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from hashlib import sha256
from typing import Protocol, TypeAlias

from app.core.auth import DEFAULT_PLAN, OpenAIAuthClaims, extract_id_token_claims
from app.core.auth.refresh import RefreshError, TokenRefreshResult, refresh_access_token, should_refresh
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus


class AccountsRepositoryPort(Protocol):
    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
    ) -> bool: ...

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
    ) -> bool: ...


class RefreshAdmissionLeasePort(Protocol):
    def release(self) -> None: ...


logger = logging.getLogger(__name__)


_RefreshSingleflightKey: TypeAlias = tuple[str, str]


class _RefreshSingleflight:
    def __init__(self) -> None:
        self._inflight: dict[_RefreshSingleflightKey, asyncio.Task[Account]] = {}
        self._recent_failures: dict[_RefreshSingleflightKey, tuple[float, tuple[str, str, bool]]] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        key: _RefreshSingleflightKey,
        factory: Callable[[], Coroutine[object, object, Account]],
    ) -> Account:
        account_id = key[0]
        async with self._lock:
            self._purge_stale_versions(account_id, keep_key=key)
            cached_failure = self._recent_failures.get(key)
            if cached_failure is not None:
                expires_at, failure = cached_failure
                if expires_at > time.monotonic():
                    code, message, is_permanent = failure
                    raise RefreshError(code, message, is_permanent)
                self._recent_failures.pop(key, None)
            task = self._inflight.get(key)
            if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
                task = asyncio.create_task(factory())
                self._inflight[key] = task
                task.add_done_callback(lambda done, *, cache_key=key: self._schedule_complete(cache_key, done))
        return await asyncio.shield(task)

    def _schedule_complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        asyncio.create_task(self._complete(key, task))

    async def _complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        try:
            async with self._lock:
                current = self._inflight.get(key)
                if current is task:
                    self._inflight.pop(key, None)
                if task.cancelled():
                    self._recent_failures.pop(key, None)
                    return
                try:
                    task.result()
                except RefreshError as exc:
                    ttl = max(0.0, float(get_settings().proxy_refresh_failure_cooldown_seconds))
                    if ttl > 0:
                        self._recent_failures[key] = (
                            time.monotonic() + ttl,
                            (exc.code, exc.message, exc.is_permanent),
                        )
                except BaseException:
                    self._recent_failures.pop(key, None)
                else:
                    self._recent_failures.pop(key, None)
        except BaseException:
            logger.exception("Refresh singleflight completion cleanup failed key=%s", key)

    def _purge_stale_versions(self, account_id: str, *, keep_key: _RefreshSingleflightKey) -> None:
        stale_failures = [key for key in self._recent_failures if key[0] == account_id and key != keep_key]
        for key in stale_failures:
            self._recent_failures.pop(key, None)
        stale_inflight = [
            key for key, task in self._inflight.items() if key[0] == account_id and key != keep_key and task.done()
        ]
        for key in stale_inflight:
            self._inflight.pop(key, None)

    def clear(self) -> None:
        self._inflight.clear()
        self._recent_failures.clear()


_REFRESH_SINGLEFLIGHT = _RefreshSingleflight()


class AuthManager:
    def __init__(
        self,
        repo: AccountsRepositoryPort,
        *,
        acquire_refresh_admission: Callable[[], Awaitable[RefreshAdmissionLeasePort]] | None = None,
    ) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()
        self._acquire_refresh_admission = acquire_refresh_admission

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        if force or should_refresh(account.last_refresh):
            account = await _REFRESH_SINGLEFLIGHT.run(
                _refresh_singleflight_key(self._encryptor, account),
                lambda: self.refresh_account(account),
            )
        return await self._ensure_chatgpt_account_id(account)

    async def refresh_account(self, account: Account) -> Account:
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        try:
            result = await self._refresh_tokens(refresh_token)
        except RefreshError as exc:
            if exc.is_permanent:
                latest = await self._repo.get_by_id(account.id)
                if latest is not None and _refresh_token_material_changed(
                    self._encryptor,
                    latest.refresh_token_encrypted,
                    account.refresh_token_encrypted,
                ):
                    raise RefreshError(exc.code, exc.message, False) from exc
                reason = PERMANENT_FAILURE_CODES.get(exc.code, exc.message)
                await self._repo.update_status(account.id, AccountStatus.DEACTIVATED, reason)
                account.status = AccountStatus.DEACTIVATED
                account.deactivation_reason = reason
            raise

        account.access_token_encrypted = self._encryptor.encrypt(result.access_token)
        account.refresh_token_encrypted = self._encryptor.encrypt(result.refresh_token)
        account.id_token_encrypted = self._encryptor.encrypt(result.id_token)
        account.last_refresh = utcnow()
        if result.account_id:
            account.chatgpt_account_id = result.account_id
        if result.plan_type is not None:
            account.plan_type = coerce_account_plan_type(
                result.plan_type,
                account.plan_type or DEFAULT_PLAN,
            )
        elif not account.plan_type:
            account.plan_type = DEFAULT_PLAN
        if result.email:
            account.email = result.email

        await self._repo.update_tokens(
            account.id,
            access_token_encrypted=account.access_token_encrypted,
            refresh_token_encrypted=account.refresh_token_encrypted,
            id_token_encrypted=account.id_token_encrypted,
            last_refresh=account.last_refresh,
            plan_type=account.plan_type,
            email=account.email,
            chatgpt_account_id=account.chatgpt_account_id,
        )
        return account

    async def _refresh_tokens(self, refresh_token: str) -> TokenRefreshResult:
        refresh_lease: RefreshAdmissionLeasePort | None = None
        if self._acquire_refresh_admission is not None:
            refresh_lease = await self._acquire_refresh_admission()
        try:
            return await refresh_access_token(refresh_token)
        finally:
            if refresh_lease is not None:
                refresh_lease.release()

    async def _ensure_chatgpt_account_id(self, account: Account) -> Account:
        if account.chatgpt_account_id:
            return account
        try:
            id_token = self._encryptor.decrypt(account.id_token_encrypted)
        except Exception:
            return account
        raw_account_id = _chatgpt_account_id_from_id_token(id_token)
        if not raw_account_id:
            return account

        account.chatgpt_account_id = raw_account_id
        try:
            await self._repo.update_tokens(
                account.id,
                access_token_encrypted=account.access_token_encrypted,
                refresh_token_encrypted=account.refresh_token_encrypted,
                id_token_encrypted=account.id_token_encrypted,
                last_refresh=account.last_refresh,
                plan_type=account.plan_type,
                email=account.email,
                chatgpt_account_id=raw_account_id,
            )
        except Exception:
            logger.warning("Failed to persist chatgpt_account_id account_id=%s", account.id, exc_info=True)
        return account


def _chatgpt_account_id_from_id_token(id_token: str) -> str | None:
    claims = extract_id_token_claims(id_token)
    auth_claims = claims.auth or OpenAIAuthClaims()
    return auth_claims.chatgpt_account_id or claims.chatgpt_account_id


def _refresh_singleflight_key(encryptor: TokenEncryptor, account: Account) -> _RefreshSingleflightKey:
    return (account.id, _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted))


def _refresh_token_material_changed(
    encryptor: TokenEncryptor,
    latest_refresh_token_encrypted: bytes,
    current_refresh_token_encrypted: bytes,
) -> bool:
    return _refresh_token_material_fingerprint(
        encryptor,
        latest_refresh_token_encrypted,
    ) != _refresh_token_material_fingerprint(
        encryptor,
        current_refresh_token_encrypted,
    )


def _refresh_token_material_fingerprint(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str:
    try:
        material = encryptor.decrypt(refresh_token_encrypted).encode("utf-8")
    except Exception:
        material = refresh_token_encrypted
    return sha256(material).hexdigest()


def _clear_refresh_singleflight_state() -> None:
    _REFRESH_SINGLEFLIGHT.clear()
