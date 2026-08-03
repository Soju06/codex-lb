from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    clean_account_identity_part,
    extract_id_token_claims,
    resolve_seat_identity,
    resolve_seat_identity_aliases,
    token_expiry_epoch_ms,
)
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.crypto import TokenEncryptor
from app.core.exceptions import ProxyAuthError, ProxyRateLimitError, ProxyUpstreamError
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.usage.models import UsagePayload
from app.db.models import Account
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository

_POSITIVE_CACHE_TTL_SECONDS = 60.0
_DENIAL_CACHE_TTL_SECONDS = 5.0
_CACHE_MAX_ENTRIES = 256


@dataclass(frozen=True, slots=True)
class VerifiedCodexOAuthIdentity:
    principal_id: str
    caller_account_id: str | None
    chatgpt_account_id: str
    usage_payload: UsagePayload
    route: ResolvedUpstreamRoute | None


@dataclass(frozen=True, slots=True)
class _CachedDenial:
    message: str


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    value: VerifiedCodexOAuthIdentity | _CachedDenial
    expires_at: float


_identity_cache: OrderedDict[str, _CacheEntry] = OrderedDict()
_identity_inflight: dict[str, asyncio.Task[VerifiedCodexOAuthIdentity]] = {}
_identity_lock = asyncio.Lock()


def clear_codex_oauth_identity_cache() -> None:
    """Clear process-local identity state (used by tests and lifecycle resets)."""

    _identity_cache.clear()
    _identity_inflight.clear()


async def resolve_verified_codex_oauth_identity(
    authorization: str | None,
    chatgpt_account_id: str | None,
) -> VerifiedCodexOAuthIdentity:
    token = _extract_bearer_token(authorization)
    if token is None:
        raise ProxyAuthError("Missing ChatGPT token in Authorization header")

    normalized_account_id = clean_account_identity_part(chatgpt_account_id)
    if normalized_account_id is None:
        raise ProxyAuthError("Missing chatgpt-account-id header")

    cache_key = _credential_digest(token, normalized_account_id)
    async with _identity_lock:
        cached = _get_cached_locked(cache_key)
        if cached is not None:
            if isinstance(cached, _CachedDenial):
                raise ProxyAuthError(cached.message)
            return cached

        task = _identity_inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(
                _validate_and_cache_identity(
                    cache_key=cache_key,
                    access_token=token,
                    chatgpt_account_id=normalized_account_id,
                )
            )
            _identity_inflight[cache_key] = task
            task.add_done_callback(lambda completed, key=cache_key: _remove_inflight(key, completed))

    return await asyncio.shield(task)


async def _validate_and_cache_identity(
    *,
    cache_key: str,
    access_token: str,
    chatgpt_account_id: str,
) -> VerifiedCodexOAuthIdentity:
    try:
        identity = await _validate_identity_uncached(
            access_token=access_token,
            chatgpt_account_id=chatgpt_account_id,
        )
    except ProxyAuthError as exc:
        async with _identity_lock:
            _set_cached_locked(
                cache_key,
                _CachedDenial(exc.message),
                ttl_seconds=_DENIAL_CACHE_TTL_SECONDS,
            )
        raise

    ttl_seconds = _positive_cache_ttl(access_token)
    if ttl_seconds > 0:
        async with _identity_lock:
            _set_cached_locked(cache_key, identity, ttl_seconds=ttl_seconds)
    return identity


async def _validate_identity_uncached(
    *,
    access_token: str,
    chatgpt_account_id: str,
) -> VerifiedCodexOAuthIdentity:
    stable_principal = resolve_seat_identity(extract_id_token_claims(access_token))
    validation_account_id: str | None = None
    validation_route: ResolvedUpstreamRoute | None = None
    async with get_background_session() as session:
        candidates = await AccountsRepository(session).list_eligible_by_chatgpt_account_id(chatgpt_account_id)
        validation_candidates = _prefer_token_seat_alias(candidates, access_token, TokenEncryptor())
        if validation_candidates:
            validation_account_id = validation_candidates[0].id
            validation_route = await _resolve_route(session, validation_account_id)
        elif stable_principal is None:
            raise ProxyAuthError("Unknown or ambiguous ChatGPT identity")
        else:
            validation_route = await _resolve_route(session, None)

    try:
        usage_payload = await fetch_usage(
            access_token=access_token,
            account_id=chatgpt_account_id,
            route=validation_route,
            allow_direct_egress=validation_route is None,
        )
    except UsageFetchError as exc:
        if exc.status_code == 429:
            raise ProxyRateLimitError("ChatGPT credential validation rate limited") from exc
        if exc.status_code in (401, 403):
            raise ProxyAuthError("Invalid ChatGPT token or chatgpt-account-id") from exc
        raise ProxyUpstreamError("Unable to validate ChatGPT credentials at this time") from exc

    async with get_background_session() as session:
        candidates = await AccountsRepository(session).list_eligible_by_chatgpt_account_id(chatgpt_account_id)
        caller = _resolve_unique_caller(
            access_token=access_token,
            usage_payload=usage_payload,
            candidates=candidates,
            encryptor=TokenEncryptor(),
        )
        if caller is None and stable_principal is None:
            raise ProxyAuthError("Unknown or ambiguous ChatGPT identity")
        caller_account_id = caller.id if caller is not None else None
        if stable_principal is not None:
            principal_id = f"principal:{stable_principal}"
        elif caller_account_id is not None:
            principal_id = caller_account_id
        else:
            raise ProxyAuthError("Unknown or ambiguous ChatGPT identity")

        if caller_account_id is None:
            route = validation_route
        else:
            route = (
                validation_route
                if caller_account_id == validation_account_id
                else await _resolve_route(session, caller_account_id)
            )

    return VerifiedCodexOAuthIdentity(
        principal_id=principal_id,
        caller_account_id=caller_account_id,
        chatgpt_account_id=chatgpt_account_id,
        usage_payload=usage_payload,
        route=route,
    )


async def _resolve_route(session: AsyncSession, account_id: str | None) -> ResolvedUpstreamRoute | None:
    try:
        return await resolve_upstream_route(
            session,
            account_id=account_id,
            operation="usage_identity",
            scope="account",
            encryptor=TokenEncryptor(),
        )
    except UpstreamProxyRouteError as exc:
        raise ProxyUpstreamError("Unable to resolve upstream proxy route for ChatGPT credentials") from exc


def _resolve_unique_caller(
    *,
    access_token: str,
    usage_payload: UsagePayload,
    candidates: list[Account],
    encryptor: TokenEncryptor,
) -> Account | None:
    contextual_candidates = _prefer_exact_verified_workspace(candidates, usage_payload)
    contextual_candidates = _prefer_token_seat_alias(contextual_candidates, access_token, encryptor)
    return contextual_candidates[0] if len(contextual_candidates) == 1 else None


def _prefer_token_seat_alias(
    candidates: list[Account],
    access_token: str,
    encryptor: TokenEncryptor,
) -> list[Account]:
    token_aliases = resolve_seat_identity_aliases(extract_id_token_claims(access_token))
    if not token_aliases:
        return candidates
    return [
        account for account in candidates if token_aliases.intersection(_account_identity_aliases(account, encryptor))
    ]


def _prefer_exact_verified_workspace(candidates: list[Account], usage_payload: UsagePayload) -> list[Account]:
    verified_workspace_id = clean_account_identity_part(usage_payload.workspace_id)
    if verified_workspace_id is not None:
        exact_workspace = [
            account
            for account in candidates
            if clean_account_identity_part(account.workspace_id) == verified_workspace_id
        ]
        if exact_workspace:
            return exact_workspace

    verified_label = clean_account_identity_part(usage_payload.workspace_label)
    if verified_workspace_id is None and verified_label is not None:
        exact_label = [
            account
            for account in candidates
            if (clean_account_identity_part(account.workspace_label) or "").casefold() == verified_label.casefold()
        ]
        if exact_label:
            return exact_label

    return [account for account in candidates if _matches_verified_workspace(account, usage_payload)]


def _matches_verified_workspace(account: Account, usage_payload: UsagePayload) -> bool:
    verified_workspace_id = clean_account_identity_part(usage_payload.workspace_id)
    account_workspace_id = clean_account_identity_part(account.workspace_id)
    if (
        verified_workspace_id is not None
        and account_workspace_id is not None
        and verified_workspace_id != account_workspace_id
    ):
        return False

    verified_label = clean_account_identity_part(usage_payload.workspace_label)
    account_label = clean_account_identity_part(account.workspace_label)
    return not (
        verified_workspace_id is None
        and verified_label is not None
        and account_label is not None
        and verified_label.casefold() != account_label.casefold()
    )


def _account_identity_aliases(account: Account, encryptor: TokenEncryptor) -> frozenset[str]:
    aliases = set()
    account_user_id = clean_account_identity_part(account.chatgpt_user_id)
    if account_user_id is not None:
        aliases.add(account_user_id)
    try:
        id_token = encryptor.decrypt(account.id_token_encrypted)
    except Exception:
        return frozenset(aliases)
    aliases.update(resolve_seat_identity_aliases(extract_id_token_claims(id_token)))
    return frozenset(aliases)


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    value = authorization.strip()
    prefix = "bearer "
    if not value.lower().startswith(prefix):
        return None
    return clean_account_identity_part(value[len(prefix) :])


def _credential_digest(access_token: str, chatgpt_account_id: str) -> str:
    material = f"{access_token}\0{chatgpt_account_id}".encode()
    return hashlib.sha256(material).hexdigest()


def _positive_cache_ttl(access_token: str) -> float:
    expires_at_ms = token_expiry_epoch_ms(access_token)
    if expires_at_ms is None:
        return _POSITIVE_CACHE_TTL_SECONDS
    remaining_seconds = expires_at_ms / 1000.0 - time.time()
    return max(0.0, min(_POSITIVE_CACHE_TTL_SECONDS, remaining_seconds))


def _get_cached_locked(cache_key: str) -> VerifiedCodexOAuthIdentity | _CachedDenial | None:
    entry = _identity_cache.get(cache_key)
    if entry is None:
        return None
    if entry.expires_at <= time.monotonic():
        _identity_cache.pop(cache_key, None)
        return None
    _identity_cache.move_to_end(cache_key)
    return entry.value


def _set_cached_locked(
    cache_key: str,
    value: VerifiedCodexOAuthIdentity | _CachedDenial,
    *,
    ttl_seconds: float,
) -> None:
    _identity_cache[cache_key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl_seconds)
    _identity_cache.move_to_end(cache_key)
    while len(_identity_cache) > _CACHE_MAX_ENTRIES:
        _identity_cache.popitem(last=False)


def _remove_inflight(
    cache_key: str,
    completed: asyncio.Task[VerifiedCodexOAuthIdentity],
) -> None:
    if _identity_inflight.get(cache_key) is completed:
        _identity_inflight.pop(cache_key, None)
