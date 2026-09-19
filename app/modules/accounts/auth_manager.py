from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol, TypeAlias

from app.core.auth import DEFAULT_PLAN, token_expiry_epoch_ms
from app.core.auth.refresh import (
    RefreshError,
    TokenRefreshResult,
    classify_refresh_error,
    refresh_access_token,
    should_refresh,
)
from app.core.balancer import account_status_for_permanent_failure, permanent_failure_reason
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.providers import OPENAI_PROVIDER_NAME, Provider, ProviderLookupError, get_provider
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session


class AccountsRepositoryPort(Protocol):
    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def reload_by_id(self, account_id: str) -> Account | None: ...

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool: ...

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes | None,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool: ...

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool: ...


class RefreshAdmissionLeasePort(Protocol):
    def release(self) -> None: ...


logger = logging.getLogger(__name__)
_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 300
# Non-owners cannot refresh, so they use a much smaller bar: still-clock-skew-tolerant,
# but not the proactive optimization margin above (which only the owner can act on).
_ACCESS_TOKEN_HARD_EXPIRY_SKEW_SECONDS = 30


class AccountNotOwnedError(Exception):
    """Raised when a refresh is required for an account this instance does not own.

    Instance federation restricts OAuth refresh execution to a single owner
    instance per account (openspec instance-federation): non-owners must
    never call the provider's refresh endpoint, regardless of token age.
    """

    def __init__(self, account_id: str, owner_instance: str | None, local_instance_id: str) -> None:
        message = (
            f"Account {account_id} is owned by instance {owner_instance!r}, not the "
            f"local instance {local_instance_id!r}; refusing to refresh"
        )
        super().__init__(message)
        self.account_id = account_id
        self.owner_instance = owner_instance
        self.local_instance_id = local_instance_id


def is_locally_owned(account: Account, settings: Settings) -> bool:
    return account.owner_instance is None or account.owner_instance == settings.local_instance_id


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
            if task is not None and task.done() and not task.cancelled() and task.exception() is None:
                pass
            elif task is None or task.done():
                task = asyncio.create_task(factory())
                self._inflight[key] = task
                task.add_done_callback(lambda done, *, cache_key=key: self._schedule_complete(cache_key, done))
        assert task is not None
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
                    if ttl > 0 and not exc.transport_error:
                        self._recent_failures[key] = (
                            time.monotonic() + ttl,
                            (exc.code, exc.message, exc.is_permanent),
                        )
                    else:
                        self._recent_failures.pop(key, None)
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
        refresh_repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepositoryPort]] | None = None,
    ) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()
        self._acquire_refresh_admission = acquire_refresh_admission
        # Optional factory yielding a *fresh* accounts repo (own DB session) for
        # the detached, shielded refresh task. When set, the singleflight body
        # runs against this session instead of the request-scoped `repo`, so a
        # caller cancelled by a client disconnect cannot close the session out
        # from under the still-running refresh task and strand a pooled
        # connection. See _run_refresh.
        self._refresh_repo_factory = refresh_repo_factory

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        settings = get_settings()
        if not is_locally_owned(account, settings):
            # Non-owners never call the provider's refresh endpoint (ownership gate).
            # A mirrored token within the proactive margin but not yet actually
            # expired is still usable — only serve AccountNotOwnedError when the
            # token is genuinely unusable, or the caller demands a guaranteed-fresh
            # one via force=True.
            if force or access_token_hard_expired(self._encryptor, account):
                raise AccountNotOwnedError(account.id, account.owner_instance, settings.local_instance_id)
            return await self._ensure_chatgpt_account_id(account)

        if force or _account_needs_refresh(self._encryptor, account):
            account = await _REFRESH_SINGLEFLIGHT.run(
                _refresh_singleflight_key(self._encryptor, account),
                lambda: self._run_refresh(account),
            )
        return await self._ensure_chatgpt_account_id(account)

    async def _run_refresh(self, account: Account) -> Account:
        """Singleflight body for token refresh.

        Runs inside a detached task that the singleflight keeps alive with
        ``asyncio.shield`` (so concurrent waiters share one refresh and a
        cancelled waiter does not abort it). Because the task outlives the
        caller, it MUST NOT use the caller's request-scoped session: when a
        client disconnects, the caller is cancelled and its
        ``async with get_background_session()`` closes that session, while this
        shielded task keeps running and would then touch a closed,
        concurrently-finalized ``AsyncSession`` (not safe for concurrent use) —
        stranding a pooled connection that never returns. When a
        ``refresh_repo_factory`` is provided, open a fresh session here so the
        refresh write is fully self-contained; otherwise fall back to the bound
        repo (callers whose session is not client-cancellable, e.g. the usage
        refresh scheduler).
        """
        if self._refresh_repo_factory is None:
            return await self.refresh_account(account)
        async with self._refresh_repo_factory() as repo:
            owned = AuthManager(repo, acquire_refresh_admission=self._acquire_refresh_admission)
            return await owned.refresh_account(account)

    async def refresh_account(self, account: Account) -> Account:
        try:
            provider = get_provider(account.provider)
        except ProviderLookupError as exc:
            raise RefreshError("unsupported_provider", str(exc), True) from exc

        expected_refresh_token_encrypted = account.refresh_token_encrypted
        latest = await self._repo.reload_by_id(account.id)
        if latest is not None and _refresh_token_material_changed(
            self._encryptor, latest.refresh_token_encrypted, expected_refresh_token_encrypted
        ):
            # A delayed caller must not replay a token already rotated by another request.
            return latest
        if latest is not None:
            account = latest
            expected_refresh_token_encrypted = latest.refresh_token_encrypted
        refresh_token = self._encryptor.decrypt(expected_refresh_token_encrypted)
        try:
            result = await self._refresh_tokens(refresh_token, account=account, provider=provider)
        except RefreshError as exc:
            is_permanent = exc.is_permanent or classify_refresh_error(exc.code)
            if is_permanent:
                exc.is_permanent = True
                latest = await self._repo.reload_by_id(account.id)
                if latest is not None and _refresh_token_material_changed(
                    self._encryptor,
                    latest.refresh_token_encrypted,
                    expected_refresh_token_encrypted,
                ):
                    return latest
                reason = permanent_failure_reason(exc.code)
                status = account_status_for_permanent_failure(exc.code)
                status_updated = await self._repo.update_status(
                    account.id,
                    status,
                    reason,
                    expected_refresh_token_encrypted=(
                        latest.refresh_token_encrypted if latest is not None else expected_refresh_token_encrypted
                    ),
                )
                if not status_updated:
                    current = await self._repo.reload_by_id(account.id)
                    if current is not None and _refresh_token_material_changed(
                        self._encryptor,
                        current.refresh_token_encrypted,
                        expected_refresh_token_encrypted,
                    ):
                        return current
                    raise
                account.status = status
                account.deactivation_reason = reason
                logger.warning(
                    "OAuth refresh requires reauthentication account_id=%s provider=%s code=%s",
                    account.id,
                    account.provider,
                    exc.code,
                )
            raise

        account.access_token_encrypted = self._encryptor.encrypt(result.access_token)
        account.refresh_token_encrypted = self._encryptor.encrypt(result.refresh_token)
        if result.id_token:
            account.id_token_encrypted = self._encryptor.encrypt(result.id_token)
        elif provider.requires_id_token:
            raise RefreshError("invalid_response", "Refresh response missing id token", False)
        else:
            account.id_token_encrypted = None
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
        incoming_workspace_id = _clean_optional(result.workspace_id)
        current_workspace_id = _clean_optional(account.workspace_id)
        next_workspace_id = current_workspace_id
        if incoming_workspace_id and current_workspace_id and current_workspace_id != incoming_workspace_id:
            logger.warning(
                "Refresh payload reported workspace_id=%s for account_id=%s while existing "
                "workspace_id=%s is already set; keeping slot identity",
                incoming_workspace_id,
                account.id,
                current_workspace_id,
            )
            next_workspace_id = current_workspace_id
        elif not current_workspace_id and incoming_workspace_id:
            slot_taken = await self._repo.workspace_slot_taken(
                account_id=account.id,
                email=account.email,
                chatgpt_account_id=account.chatgpt_account_id,
                workspace_id=incoming_workspace_id,
            )
            if slot_taken:
                logger.warning(
                    "Refresh payload reported workspace_id=%s for legacy account_id=%s, but that slot "
                    "is already owned by another account; keeping unknown workspace",
                    incoming_workspace_id,
                    account.id,
                )
            else:
                next_workspace_id = incoming_workspace_id
                account.workspace_id = next_workspace_id
        workspace_matches_current_slot = incoming_workspace_id is None or incoming_workspace_id == next_workspace_id
        if workspace_matches_current_slot and result.workspace_label:
            account.workspace_label = result.workspace_label
        if workspace_matches_current_slot and result.seat_type:
            account.seat_type = result.seat_type

        tokens_updated = await self._repo.update_tokens(
            account.id,
            access_token_encrypted=account.access_token_encrypted,
            refresh_token_encrypted=account.refresh_token_encrypted,
            id_token_encrypted=account.id_token_encrypted,
            last_refresh=account.last_refresh,
            plan_type=account.plan_type,
            email=account.email,
            chatgpt_account_id=account.chatgpt_account_id,
            workspace_id=next_workspace_id,
            workspace_label=account.workspace_label,
            seat_type=account.seat_type,
            expected_refresh_token_encrypted=expected_refresh_token_encrypted,
        )
        if not tokens_updated:
            latest = await self._repo.reload_by_id(account.id)
            if latest is not None and latest.refresh_token_encrypted != expected_refresh_token_encrypted:
                return latest
            raise RefreshError(
                "refresh_persistence_conflict",
                "Refreshed credentials could not be stored because the account changed concurrently",
                False,
            )
        return account

    async def _refresh_tokens(
        self,
        refresh_token: str,
        *,
        account: Account,
        provider: Provider,
    ) -> TokenRefreshResult:
        refresh_lease: RefreshAdmissionLeasePort | None = None
        if self._acquire_refresh_admission is not None:
            refresh_lease = await self._acquire_refresh_admission()
        try:
            if provider.name != OPENAI_PROVIDER_NAME:
                return await provider.refresh_access_token(refresh_token)
            async with get_background_session() as session:
                try:
                    route = await resolve_upstream_route(
                        session,
                        account_id=account.id,
                        operation="token_refresh",
                        scope="account",
                        encryptor=self._encryptor,
                    )
                except UpstreamProxyRouteError as exc:
                    raise RefreshError(
                        "upstream_proxy_unavailable",
                        f"Upstream proxy route unavailable: {exc.reason}",
                        False,
                        transport_error=True,
                        upstream_proxy_fail_closed_reason=exc.reason,
                    ) from exc
            return await _call_with_supported_optional_kwargs(
                refresh_access_token,
                refresh_token,
                optional_kwargs={
                    "route": route,
                    "allow_direct_egress": route is None,
                },
            )
        finally:
            if refresh_lease is not None:
                refresh_lease.release()

    async def _ensure_chatgpt_account_id(self, account: Account) -> Account:
        if account.chatgpt_account_id:
            return account
        if account.provider != OPENAI_PROVIDER_NAME or account.id_token_encrypted is None:
            return account
        try:
            id_token = self._encryptor.decrypt(account.id_token_encrypted)
        except Exception:
            return account
        raw_account_id = get_provider(account.provider).account_metadata_from_id_token(id_token).account_id
        if not raw_account_id:
            return account

        expected_refresh_token_encrypted = account.refresh_token_encrypted
        account.chatgpt_account_id = raw_account_id
        try:
            updated = await self._repo.update_tokens(
                account.id,
                access_token_encrypted=account.access_token_encrypted,
                refresh_token_encrypted=account.refresh_token_encrypted,
                id_token_encrypted=account.id_token_encrypted,
                last_refresh=account.last_refresh,
                plan_type=account.plan_type,
                email=account.email,
                chatgpt_account_id=raw_account_id,
                workspace_id=account.workspace_id,
                workspace_label=account.workspace_label,
                seat_type=account.seat_type,
                expected_refresh_token_encrypted=expected_refresh_token_encrypted,
            )
            if not updated:
                latest = await self._repo.reload_by_id(account.id)
                if latest is not None:
                    return latest
        except Exception:
            logger.warning("Failed to persist chatgpt_account_id account_id=%s", account.id, exc_info=True)
        return account


def _chatgpt_account_id_from_id_token(id_token: str) -> str | None:
    return get_provider(OPENAI_PROVIDER_NAME).account_metadata_from_id_token(id_token).account_id


def _refresh_singleflight_key(encryptor: TokenEncryptor, account: Account) -> _RefreshSingleflightKey:
    return (account.id, _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted))


def _expires_within(expires_ms: int, *, margin_seconds: float, now: datetime | None = None) -> bool:
    """True when expires_ms is within margin_seconds of `now` (or already past)."""
    current = to_utc_naive(now) if now is not None else utcnow()
    threshold_ms = naive_utc_to_epoch(current) * 1000 + (margin_seconds * 1000)
    return expires_ms <= threshold_ms


def _access_token_needs_refresh(
    encryptor: TokenEncryptor,
    account: Account,
    *,
    now: datetime | None = None,
) -> bool:
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
    except Exception:
        return False
    expires_ms = token_expiry_epoch_ms(access_token)
    if expires_ms is None:
        return False
    return _expires_within(expires_ms, margin_seconds=_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS, now=now)


def access_token_hard_expired(
    encryptor: TokenEncryptor,
    account: Account,
    *,
    now: datetime | None = None,
) -> bool:
    """True when the access token is missing, undecryptable, or has actually passed
    its expiry (small clock-skew grace only — see _ACCESS_TOKEN_HARD_EXPIRY_SKEW_SECONDS).

    Unlike _access_token_needs_refresh, this ignores the larger proactive-refresh
    margin, which is an owner-only optimization: a mirrored token within that margin
    but not yet expired is still perfectly usable by a non-owner that cannot refresh
    it. This is the bar non-owned accounts (ensure_fresh gate, selection filtering)
    must clear to stay usable.
    """
    if not account.access_token_encrypted:
        return True
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
    except Exception:
        return True
    expires_ms = token_expiry_epoch_ms(access_token)
    if expires_ms is None:
        return False
    return _expires_within(expires_ms, margin_seconds=_ACCESS_TOKEN_HARD_EXPIRY_SKEW_SECONDS, now=now)


def _account_needs_refresh(encryptor: TokenEncryptor, account: Account, *, now: datetime | None = None) -> bool:
    current = to_utc_naive(now) if now is not None else utcnow()
    if _access_token_needs_refresh(encryptor, account, now=current):
        return True
    try:
        provider = get_provider(account.provider)
    except ProviderLookupError:
        return should_refresh(account.last_refresh, now=current)
    interval_seconds = provider.access_token_refresh_interval_seconds
    if interval_seconds is not None:
        last = to_utc_naive(account.last_refresh)
        return current - last > timedelta(seconds=interval_seconds)
    return should_refresh(account.last_refresh, now=current)


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


def _clean_optional(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


async def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> Any:
    kwargs = dict(required_kwargs)
    kwargs.update(optional_kwargs)
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    accepts_var_keyword = signature is not None and any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if signature is not None and not accepts_var_keyword:
        for name in optional_kwargs:
            if name not in signature.parameters:
                kwargs.pop(name, None)
    return await func(*args, **kwargs)


def _clear_refresh_singleflight_state() -> None:
    _REFRESH_SINGLEFLIGHT.clear()
