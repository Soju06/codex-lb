from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, AsyncContextManager
from urllib.parse import urljoin

import aiohttp
from pydantic import ValidationError

from app.core.anthropic.models import (
    AnthropicErrorEvent,
    AnthropicMessageRequest,
    AnthropicUsage,
    merge_usage_values,
)
from app.core.anthropic.parsing import parse_sse_event
from app.core.auth.refresh import RefreshError, classify_refresh_error
from app.core.balancer.types import UpstreamError
from app.core.clients.http import lease_http_session
from app.core.clients.proxy import filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, GLM_PROVIDER_NAME
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import naive_utc_to_epoch
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.credits import (
    CREDITS_USAGE_WINDOW,
    OPENROUTER_CREDITS_QUOTA_KEY,
    OPENROUTER_PROVIDER_NAME,
    OPENROUTER_UPSTREAM_BASE_URL,
    SUPPORTS_UPSTREAM_COUNT_TOKENS,
    credits_exhausted,
    window_from_usage,
)
from app.modules.api_keys.service import ApiKeyData, ApiKeysService, ApiKeyUsageReservationData
from app.modules.proxy._service.support import _request_log_useragent_fields
from app.modules.proxy.claude_codex_bridge import estimate_claude_input_tokens
from app.modules.proxy.load_balancer import LoadBalancer, selectable_accounts
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.usage.additional_quota_keys import get_additional_quota_definition_for_model

logger = logging.getLogger(__name__)

_STREAM_CHUNK_SIZE = 8192
_MAX_SELECTION_ATTEMPTS = 4
_ANTHROPIC_COOLDOWN_WINDOW = "primary"
_ANTHROPIC_COOLDOWN_FEATURE = "anthropic_messages"
_ANTHROPIC_DEFAULT_COOLDOWN_SECONDS = 60
# Pool-wide last-resort horizon for requested-quota cooldowns. Upstream
# request-rate 429s often arrive with no reset headers and get the 60s
# default cooldown above; with a small pool those overlap into a permanent
# pool-wide 429 even while every usage window reports headroom. A cooldown
# resetting within this horizon is treated as transient (bypassable when the
# whole pool is blocked); longer, header-derived resets stay authoritative.
_ANTHROPIC_TRANSIENT_COOLDOWN_BYPASS_HORIZON_SECONDS = 120
_ANTHROPIC_FAST_QUOTA_KEY = "anthropic_fast"
# Dedicated marker for a successful response that Anthropic served from paid
# extra usage after rejecting the subscription window. Keeping this distinct
# from request-quota 429 cooldowns lets the explicit paid fallback bypass only
# the credit-billing tripwire, never a real upstream rejection.
_ANTHROPIC_EXTRA_USAGE_QUOTA_KEY = "anthropic_extra_usage"
# Mirrored in app/modules/accounts/pulse.py (ANTHROPIC_FABLE_ACCESS_QUOTA_KEY)
# — both must agree on the quota_key/window identifying the Fable-access
# probe marker written by the account pulse.
_ANTHROPIC_FABLE_ACCESS_QUOTA_KEY = "anthropic_fable_access"
_ANTHROPIC_FABLE_ACCESS_WINDOW = "primary"
# Mirrored in app/modules/usage/updater.py (write side) — both must agree on
# the quota_key/window identifying Anthropic's dedicated Fable-scoped weekly
# limit marker.
_ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"
_ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW = "primary"
# A scoped entry older than this is stale; the overall-weekly heuristic and
# probe-marker fallback apply instead (usage refreshes far more often than
# this, so staleness signals a refresh gap, not just normal cadence).
_FABLE_SCOPED_FRESH_SECONDS = 21600  # 6 hours
_ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"
_ANTHROPIC_FAST_MODE_BETA = "fast-mode-2026-02-01"
_COUNT_TOKENS_TIMEOUT_SECONDS = 30.0

# Unified rate-limit status headers observed on live OAuth responses
# (2026-07-01). A healthy subscription-covered response carries
# ``anthropic-ratelimit-unified-status: allowed``; a response served by
# billing extra-usage credits carries ``rejected`` plus
# ``anthropic-ratelimit-unified-overage-in-use: true``.
_ANTHROPIC_UNIFIED_OVERAGE_IN_USE_HEADER = "anthropic-ratelimit-unified-overage-in-use"
_ANTHROPIC_UNIFIED_STATUS_HEADERS = (
    "anthropic-ratelimit-unified-status",
    "anthropic-ratelimit-unified-5h-status",
    "anthropic-ratelimit-unified-7d-status",
)
_ANTHROPIC_UNIFIED_ALLOWED_STATUSES = frozenset({"allowed", "allowed_warning"})

# Upstream 529 "Overloaded" is a transient capacity signal, not quota or
# account health: during partial brownouts another account frequently serves
# the same request immediately. Failover retries back off briefly so a herd
# of concurrent requests does not hammer the next account in lockstep.
_OVERLOADED_BACKOFF_BASE_SECONDS = 0.25
_OVERLOADED_BACKOFF_MAX_SECONDS = 2.0
_OVERLOADED_BACKOFF_MAX_JITTER_SECONDS = 0.25


def _default_overloaded_backoff_jitter() -> float:
    return random.uniform(0.0, _OVERLOADED_BACKOFF_MAX_JITTER_SECONDS)


# Injection points so tests drive 529 failover without real sleeps.
_OVERLOADED_RETRY_SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep
_OVERLOADED_RETRY_JITTER: Callable[[], float] = _default_overloaded_backoff_jitter


def _overloaded_backoff_seconds(attempt: int) -> float:
    base = min(_OVERLOADED_BACKOFF_BASE_SECONDS * (2**attempt), _OVERLOADED_BACKOFF_MAX_SECONDS)
    return base + _OVERLOADED_RETRY_JITTER()


# Pool-exhausted wait: how streams hold for the next window instead of dying.
# Sleeps are sliced so an account freed early (cooldown cleared, account
# added) is picked up within one poll interval; jitter avoids a thundering
# herd of held streams re-attempting selection in the same instant.
_POOL_WAIT_MIN_POLL_SECONDS = 5.0
_POOL_WAIT_MAX_POLL_SECONDS = 300.0
_POOL_WAIT_MAX_JITTER_SECONDS = 5.0


def _default_pool_wait_jitter() -> float:
    return random.uniform(0.0, _POOL_WAIT_MAX_JITTER_SECONDS)


# Injection points so tests drive the hold with a fake clock — never real sleeps.
_POOL_WAIT_CLOCK: Callable[[], float] = time.time
_POOL_WAIT_SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep
_POOL_WAIT_JITTER: Callable[[], float] = _default_pool_wait_jitter


@dataclass(frozen=True, slots=True)
class AnthropicProxyStream:
    body: AsyncIterator[bytes]
    media_type: str


@dataclass(frozen=True, slots=True)
class AnthropicCountTokensResult:
    status_code: int
    body: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class _AnthropicQuotaEligibility:
    account_ids: list[str]
    blocked_count: int = 0
    next_reset_at: int | None = None
    # Exact IDs allowed to bypass a persisted primary-window RATE_LIMITED
    # status for this request. This is intentionally narrower than account_ids:
    # only the paid last-resort branch may populate it.
    paid_fallback_account_ids: frozenset[str] = frozenset()
    # Accounts past the Fable weekly threshold: preferred (burn_first) for
    # non-Fable traffic so under-threshold accounts keep Fable headroom.
    burn_first_account_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _AnthropicErrorDetails:
    message: str
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class AnthropicResolvedMessageRequest:
    payload: AnthropicMessageRequest
    provider_name: str
    quota_key: str


class AnthropicProxyError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str = "anthropic_proxy_error",
        retry_at: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retry_at = retry_at


class AnthropicProxyService:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._load_balancer = LoadBalancer(repo_factory)
        self._encryptor = TokenEncryptor()

    async def stream_messages(
        self,
        payload: AnthropicMessageRequest,
        inbound_headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
    ) -> AnthropicProxyStream:
        request_id = ensure_request_id(_request_id_from_headers(inbound_headers))
        started_at = time.monotonic()
        selected_account_ids: set[str] = set()
        media_type = "text/event-stream" if payload.stream else "application/json"
        provider_name = _messages_provider_name(payload)
        quota_key = _messages_quota_key(payload, provider_name=provider_name)
        affinity_quota_key = _messages_affinity_quota_key(payload, provider_name=provider_name)
        session_id = _anthropic_request_session_id(payload, inbound_headers)
        useragent, useragent_group = _request_log_useragent_fields(inbound_headers)
        sticky_key = _messages_sticky_key(
            payload,
            inbound_headers,
            provider_name=provider_name,
            quota_key=affinity_quota_key,
        )
        wait_enabled = (
            bool(payload.stream)
            and provider_name == ANTHROPIC_PROVIDER_NAME
            and get_settings().anthropic_pool_exhausted_wait_enabled
        )
        first_account: Account | None
        initial_wait_error: AnthropicProxyError | None = None
        try:
            first_account = await self._select_account(
                payload.model,
                provider_name=provider_name,
                exclude_account_ids=selected_account_ids,
                sticky_key=sticky_key,
                quota_key=quota_key,
            )
        except AnthropicProxyError as exc:
            if not wait_enabled or exc.retry_at is None:
                raise
            # Pool-wide exhaustion with a known reset: hold the stream open in
            # body() (keepalives flow at the route) instead of killing the
            # agent session with a 429.
            first_account = None
            initial_wait_error = exc
        if first_account is not None:
            selected_account_ids.add(first_account.id)

        async def body() -> AsyncIterator[bytes]:
            usage: AnthropicUsage | None = None
            last_account: Account | None = None
            last_error_status: int | None = None
            last_error_message: str | None = None
            streamed_bytes = False
            wait_deadline = (
                _POOL_WAIT_CLOCK() + get_settings().anthropic_pool_exhausted_wait_max_seconds if wait_enabled else None
            )
            account_override = first_account
            pending_wait_error = initial_wait_error
            while True:
                if pending_wait_error is not None:
                    await _hold_for_pool_reset(pending_wait_error, deadline=wait_deadline)
                    pending_wait_error = None
                    selected_account_ids.clear()
                    last_error_status = None
                    last_error_message = None
                try:
                    # _MAX_SELECTION_ATTEMPTS bounds one wake; every hold
                    # re-arms the full budget once the pool may have reset.
                    for attempt in range(_MAX_SELECTION_ATTEMPTS):
                        if attempt == 0 and account_override is not None:
                            account = account_override
                            account_override = None
                        else:
                            account = await self._select_account(
                                payload.model,
                                provider_name=provider_name,
                                exclude_account_ids=selected_account_ids,
                                sticky_key=sticky_key,
                                quota_key=quota_key,
                            )
                            selected_account_ids.add(account.id)
                        last_account = account
                        try:
                            access_token = await self._fresh_access_token(account)
                        except AnthropicProxyError as exc:
                            if exc.status_code != 401 or not classify_refresh_error(exc.code):
                                raise
                            await self._persist_request_log(
                                account=account,
                                provider_name=provider_name,
                                request_id=request_id,
                                model=payload.model,
                                started_at=started_at,
                                status="error",
                                error_code=exc.code,
                                error_message=exc.message,
                                api_key=api_key,
                                session_id=session_id,
                                useragent=useragent,
                                useragent_group=useragent_group,
                            )
                            last_error_status = exc.status_code
                            last_error_message = exc.message
                            continue
                        headers = _build_anthropic_headers(
                            inbound_headers,
                            access_token,
                            provider_name=provider_name,
                            fast_mode=_anthropic_fast_mode_requested(payload),
                        )
                        body_payload = payload.model_dump(mode="json", exclude_none=True)

                        async with lease_http_session() as session:
                            async with self._open_upstream_response(
                                session,
                                provider_name=provider_name,
                                headers=headers,
                                json_body=body_payload,
                            ) as resp:
                                if resp.status in {401, 403}:
                                    error_message = await _read_error_message(resp)
                                    await self._load_balancer.mark_permanent_failure(account, "invalid_api_key")
                                    await self._persist_request_log(
                                        account=account,
                                        provider_name=provider_name,
                                        request_id=request_id,
                                        model=payload.model,
                                        started_at=started_at,
                                        status="error",
                                        error_code="invalid_api_key",
                                        error_message=error_message,
                                        api_key=api_key,
                                        session_id=session_id,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                    )
                                    last_error_status = resp.status
                                    last_error_message = error_message
                                    continue
                                if resp.status == 429:
                                    error_message = await _read_error_message(resp)
                                    await self._record_quota_cooldown(
                                        account,
                                        quota_key=quota_key,
                                        error=_rate_limit_error_from_response(resp, error_message),
                                    )
                                    await self._persist_request_log(
                                        account=account,
                                        provider_name=provider_name,
                                        request_id=request_id,
                                        model=payload.model,
                                        started_at=started_at,
                                        status="error",
                                        error_code="rate_limit_exceeded",
                                        error_message=error_message,
                                        api_key=api_key,
                                        session_id=session_id,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                    )
                                    last_error_status = resp.status
                                    last_error_message = error_message
                                    continue
                                if resp.status == 529:
                                    # Transient upstream overload: another
                                    # account frequently serves the same
                                    # request immediately, so fail over inside
                                    # the attempt budget instead of raising.
                                    # No quota cooldown — 529 is not quota.
                                    error_message = await _read_error_message(resp)
                                    await self._load_balancer.record_error(account)
                                    await self._persist_request_log(
                                        account=account,
                                        provider_name=provider_name,
                                        request_id=request_id,
                                        model=payload.model,
                                        started_at=started_at,
                                        status="error",
                                        error_code="upstream_529",
                                        error_message=error_message,
                                        api_key=api_key,
                                        session_id=session_id,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                    )
                                    last_error_status = resp.status
                                    last_error_message = error_message
                                    if attempt + 1 < _MAX_SELECTION_ATTEMPTS:
                                        await _OVERLOADED_RETRY_SLEEP(_overloaded_backoff_seconds(attempt))
                                    continue
                                if resp.status >= 400:
                                    error_details = await _read_error_details(resp)
                                    await self._load_balancer.record_error(account)
                                    await self._persist_request_log(
                                        account=account,
                                        provider_name=provider_name,
                                        request_id=request_id,
                                        model=payload.model,
                                        started_at=started_at,
                                        status="error",
                                        error_code=f"upstream_{resp.status}",
                                        error_message=error_details.message,
                                        api_key=api_key,
                                        session_id=session_id,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                    )
                                    raise AnthropicProxyError(
                                        resp.status,
                                        error_details.message,
                                        code=error_details.error_type or _anthropic_error_type_for_status(resp.status),
                                    )

                                # A 200 whose unified rate-limit headers report
                                # the subscription window exhausted was served by
                                # billing extra-usage credits; cool the account
                                # down now instead of waiting ~60s for the next
                                # usage refresh to notice.
                                tripwire_error: UpstreamError | None = None
                                if provider_name == ANTHROPIC_PROVIDER_NAME:
                                    tripwire_error = _extra_usage_tripwire_error(resp.headers)
                                if tripwire_error is not None:
                                    logger.warning(
                                        "Anthropic response reports extra-usage billing; recording quota "
                                        "cooldown account_id=%s quota_key=%s request_id=%s",
                                        account.id,
                                        _ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
                                        request_id,
                                    )
                                    await self._record_quota_cooldown(
                                        account,
                                        quota_key=_ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
                                        error=tripwire_error,
                                    )

                                text_buffer = ""
                                stream_error: AnthropicErrorEvent | None = None
                                # Non-streaming responses are a single JSON document, not SSE, so the
                                # SSE collector never sees usage. Buffer the raw body and parse it at the end.
                                raw_body = bytearray() if not payload.stream else None
                                async for chunk in resp.content.iter_chunked(_STREAM_CHUNK_SIZE):
                                    if not chunk:
                                        continue
                                    chunk_bytes = bytes(chunk)
                                    if raw_body is not None:
                                        raw_body.extend(chunk_bytes)
                                    else:
                                        text_buffer, usage, chunk_error = _collect_usage_from_chunk(
                                            text_buffer, chunk_bytes, usage
                                        )
                                        if chunk_error is not None:
                                            stream_error = chunk_error
                                    streamed_bytes = True
                                    yield chunk_bytes
                                if raw_body is not None:
                                    usage = _usage_from_json_body(bytes(raw_body)) or usage

                                if stream_error is not None:
                                    # Bytes already went out, so the request cannot be
                                    # retried — but the failure must reach account
                                    # health and the request log instead of counting
                                    # as a success.
                                    stream_error_type = stream_error.error.type or "unknown"
                                    logger.warning(
                                        "anthropic_stream_error_event request_id=%s account_id=%s "
                                        "error_type=%s message=%s",
                                        request_id,
                                        account.id,
                                        stream_error_type,
                                        stream_error.error.message,
                                    )
                                    await self._load_balancer.record_error(account)
                                    await self._persist_request_log(
                                        account=account,
                                        provider_name=provider_name,
                                        request_id=request_id,
                                        model=payload.model,
                                        started_at=started_at,
                                        status="error",
                                        error_code=f"stream_error_{stream_error_type}",
                                        error_message=stream_error.error.message,
                                        api_key=api_key,
                                        session_id=session_id,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                        usage=usage,
                                    )
                                    await self._finalize_api_key_reservation(
                                        api_key_reservation,
                                        model=payload.model,
                                        usage=usage,
                                    )
                                    return

                                await self._load_balancer.record_success(account)
                                if tripwire_error is None:
                                    await self._clear_quota_cooldown(account, quota_key=quota_key)
                                await self._persist_request_log(
                                    account=account,
                                    provider_name=provider_name,
                                    request_id=request_id,
                                    model=payload.model,
                                    started_at=started_at,
                                    status="success",
                                    api_key=api_key,
                                    session_id=session_id,
                                    useragent=useragent,
                                    useragent_group=useragent_group,
                                    usage=usage,
                                )
                                await self._finalize_api_key_reservation(
                                    api_key_reservation,
                                    model=payload.model,
                                    usage=usage,
                                )
                                return
                except AnthropicProxyError as exc:
                    # Never hold once response bytes have gone out: appending a
                    # second upstream stream to a partial one would corrupt it.
                    if not streamed_bytes and _pool_wait_should_hold(
                        exc, wait_enabled=wait_enabled, deadline=wait_deadline
                    ):
                        pending_wait_error = exc
                        account_override = None
                        continue
                    if last_error_status == 529 and exc.retry_at is None:
                        # Candidates ran out mid-wake after 529s: the pool is
                        # not the problem, upstream is overloaded — surface the
                        # vendor-native error so clients retry appropriately.
                        raise AnthropicProxyError(
                            529,
                            last_error_message or "Upstream overloaded",
                            code="overloaded_error",
                        ) from exc
                    raise

                message = last_error_message or f"No available {_provider_label(provider_name)} accounts"
                # Upstream 429s above recorded cooldowns, so eligibility now knows the
                # earliest reset; surface it so clients can schedule a retry.
                eligibility = await self._provider_quota_eligibility(provider_name, quota_key)
                # A budget exhausted on 529s is an upstream overload, not a
                # local pool problem: surface the vendor-native type so
                # clients apply their own overload retry policy.
                exhausted_code = (
                    "overloaded_error" if last_error_status == 529 else _no_available_accounts_code(provider_name)
                )
                exhausted_error = AnthropicProxyError(
                    last_error_status or 503,
                    message,
                    code=exhausted_code,
                    retry_at=eligibility.next_reset_at,
                )
                if _pool_wait_should_hold(exhausted_error, wait_enabled=wait_enabled, deadline=wait_deadline):
                    pending_wait_error = exhausted_error
                    account_override = None
                    continue
                await self._release_api_key_reservation(api_key_reservation)
                await self._persist_request_log(
                    account=last_account,
                    provider_name=provider_name,
                    request_id=request_id,
                    model=payload.model,
                    started_at=started_at,
                    status="error",
                    error_code=_no_available_accounts_code(provider_name),
                    error_message=message,
                    api_key=api_key,
                    session_id=session_id,
                    useragent=useragent,
                    useragent_group=useragent_group,
                )
                raise exhausted_error

        return AnthropicProxyStream(body=body(), media_type=media_type)

    async def resolve_message_request(self, payload: AnthropicMessageRequest) -> AnthropicResolvedMessageRequest:
        provider_name = _messages_provider_name(payload)
        return AnthropicResolvedMessageRequest(
            payload=payload,
            provider_name=provider_name,
            quota_key=_messages_quota_key(payload, provider_name=provider_name),
        )

    async def count_tokens(
        self,
        body: Mapping[str, Any],
        inbound_headers: Mapping[str, str],
        *,
        model: str,
    ) -> AnthropicCountTokensResult:
        provider_name = _provider_name_for_model(model)
        if provider_name == OPENROUTER_PROVIDER_NAME and not SUPPORTS_UPSTREAM_COUNT_TOKENS:
            estimated = estimate_claude_input_tokens(body)
            return AnthropicCountTokensResult(
                status_code=200,
                body=json.dumps({"input_tokens": estimated}).encode(),
                media_type="application/json",
            )
        # Token counting is quota-free upstream and account-agnostic, so any
        # active account may serve it: selection skips sticky affinity and uses
        # a dedicated quota key that never records cooldowns, keeping message
        # cooldowns from blocking free counting. No usage, reservation, or
        # response-driven error-health state is written for this path; only
        # the shared token-refresh step may mark an account, as on every route.
        account = await self._select_account(
            model,
            provider_name=provider_name,
            exclude_account_ids=set(),
            sticky_key=None,
            quota_key=_count_tokens_quota_key(provider_name),
        )
        access_token = await self._fresh_access_token(account)
        headers = _build_anthropic_headers(inbound_headers, access_token, provider_name=provider_name)
        try:
            async with lease_http_session() as session:
                async with self._open_count_tokens_response(
                    session,
                    provider_name=provider_name,
                    headers=headers,
                    json_body=body,
                ) as resp:
                    raw = await resp.read()
                    media_type = resp.headers.get("content-type") or "application/json"
                    return AnthropicCountTokensResult(status_code=resp.status, body=raw, media_type=media_type)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise AnthropicProxyError(
                502,
                f"{_provider_label(provider_name)} count_tokens upstream request failed: {exc}",
                code="upstream_error",
            ) from exc

    def _open_upstream_response(
        self,
        session: aiohttp.ClientSession,
        *,
        provider_name: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ) -> AsyncContextManager[aiohttp.ClientResponse]:
        settings = get_settings()
        url = urljoin(_upstream_base_url(provider_name).rstrip("/") + "/", "v1/messages")
        timeout = aiohttp.ClientTimeout(
            total=settings.proxy_request_budget_seconds,
            connect=settings.upstream_connect_timeout_seconds,
        )
        return session.post(url, json=json_body, headers=headers, timeout=timeout)

    def _open_count_tokens_response(
        self,
        session: aiohttp.ClientSession,
        *,
        provider_name: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ) -> AsyncContextManager[aiohttp.ClientResponse]:
        settings = get_settings()
        url = urljoin(_upstream_base_url(provider_name).rstrip("/") + "/", "v1/messages/count_tokens")
        timeout = aiohttp.ClientTimeout(
            total=_COUNT_TOKENS_TIMEOUT_SECONDS,
            connect=settings.upstream_connect_timeout_seconds,
        )
        return session.post(url, json=json_body, headers=headers, timeout=timeout)

    async def _select_account(
        self,
        model: str,
        *,
        provider_name: str,
        exclude_account_ids: set[str],
        sticky_key: str | None,
        quota_key: str,
    ) -> Account:
        eligibility = await self._provider_quota_eligibility(provider_name, quota_key, model=model)
        if not eligibility.account_ids and eligibility.blocked_count > 0:
            reset_suffix = (
                f" Reset at {datetime.fromtimestamp(eligibility.next_reset_at).isoformat()}."
                if eligibility.next_reset_at is not None
                else ""
            )
            raise AnthropicProxyError(
                429,
                (
                    f"All {_provider_label(provider_name)} accounts are cooling down "
                    f"for quota '{quota_key}'.{reset_suffix}"
                ),
                code=_quota_cooldown_code(provider_name),
                retry_at=eligibility.next_reset_at,
            )
        settings = await get_settings_cache().get()
        # The headroom flag lives on the app config (get_settings), the same
        # source as its sibling anthropic_fable_routing_enabled, not the
        # dashboard-editable settings above.
        headroom_reallocate = get_settings().anthropic_sticky_headroom_reallocation_enabled
        selection = await self._load_balancer.select_account(
            model=model,
            provider=provider_name,
            account_ids=eligibility.account_ids,
            exclude_account_ids=exclude_account_ids,
            ignore_primary_quota_account_ids=eligibility.paid_fallback_account_ids,
            burn_first_account_ids=eligibility.burn_first_account_ids,
            burn_first_sticky_drain=bool(eligibility.burn_first_account_ids)
            and get_settings().anthropic_fable_sticky_drain_enabled,
            sticky_key=sticky_key,
            sticky_kind=StickySessionKind.CODEX_SESSION if sticky_key else None,
            prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
            prefer_earlier_reset_window="primary",
            routing_strategy="usage_weighted",
            headroom_reallocate=headroom_reallocate,
        )
        if selection.account is None:
            labeled = f"No available {_provider_label(provider_name)} accounts"
            raw_fallback = selection.error_message or labeled
            fallback = labeled if raw_fallback.startswith("No available accounts") else raw_fallback
            message, retry_at = await self._selection_failure_details(
                model=model,
                quota_key=quota_key,
                provider_name=provider_name,
                fallback=fallback,
                quota_reset_at=eligibility.next_reset_at,
                quota_blocked_count=eligibility.blocked_count,
                quota_candidate_count=len(eligibility.account_ids),
                selection_error_code=selection.error_code,
                selection_error_message=selection.error_message,
            )
            raise AnthropicProxyError(
                503,
                message,
                code=selection.error_code or _no_available_accounts_code(provider_name),
                retry_at=retry_at,
            )
        return selection.account

    async def _selection_failure_details(
        self,
        *,
        model: str,
        quota_key: str,
        provider_name: str,
        fallback: str,
        quota_reset_at: int | None = None,
        quota_blocked_count: int = 0,
        quota_candidate_count: int | None = None,
        selection_error_code: str | None = None,
        selection_error_message: str | None = None,
    ) -> tuple[str, int | None]:
        now = int(time.time())
        reset_candidates: list[int] = [quota_reset_at] if quota_reset_at is not None else []
        async with self._repo_factory() as repos:
            provider_accounts = [
                account for account in await repos.accounts.list_accounts() if account.provider.lower() == provider_name
            ]
            # Headline counts and status summary reflect only the routable
            # pool — counting canceled/deactivated rows would inflate the
            # total and imply spare capacity that can never serve traffic.
            accounts = selectable_accounts(provider_accounts)
            unusable_count = len(provider_accounts) - len(accounts)
            account_count = len(accounts)
            status_counts = Counter(_account_status_label(account) for account in accounts)
            reset_candidates.extend(int(account.reset_at) for account in accounts if account.reset_at)
            primary_usage = await repos.usage.latest_by_account(
                "primary",
                account_ids=[account.id for account in accounts],
            )
            reset_candidates.extend(
                int(entry.reset_at)
                for entry in primary_usage.values()
                if entry.reset_at and float(entry.used_percent) >= 100.0
            )
        future_resets = [candidate for candidate in reset_candidates if candidate > now]
        retry_at = min(future_resets) if future_resets else None
        if account_count == 0:
            return fallback, retry_at
        status_summary = ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        noun = "account" if account_count == 1 else "accounts"
        reset_suffix = (
            f" Limits reset at {datetime.fromtimestamp(retry_at).isoformat()}." if retry_at is not None else ""
        )
        quota_detail = _anthropic_selection_quota_detail(
            quota_key=quota_key,
            blocked_count=quota_blocked_count,
            candidate_count=quota_candidate_count,
            selection_error_code=selection_error_code,
            selection_error_message=selection_error_message,
            retry_at=retry_at,
        )
        quota_sentence = f"Model quota: {quota_detail}. " if quota_detail else ""
        stored_note = (
            f" (+{unusable_count} stored but not routable: canceled, deactivated, paused, or reauth-required)"
            if unusable_count
            else ""
        )
        message = (
            f"{account_count} {_provider_label(provider_name)} {noun} exist, but none are selectable for "
            f"{model}/{quota_key}; statuses: {status_summary}.{stored_note} "
            f"{quota_sentence}"
            f"{_other_provider_routing_message(provider_name)}{reset_suffix}"
        )
        logger.warning(
            (
                "Anthropic account selection failed model=%s quota_key=%s statuses=%s "
                "quota_blocked=%s quota_candidates=%s selection_error_code=%s retry_at=%s"
            ),
            model,
            quota_key,
            status_summary,
            quota_blocked_count,
            quota_candidate_count,
            selection_error_code,
            retry_at,
        )
        return message, retry_at

    async def _provider_quota_eligibility(
        self,
        provider_name: str,
        quota_key: str,
        *,
        model: str | None = None,
    ) -> _AnthropicQuotaEligibility:
        if provider_name == OPENROUTER_PROVIDER_NAME:
            return await self._openrouter_credit_eligibility()
        now = int(time.time())
        settings = get_settings()
        fable_routing = provider_name == ANTHROPIC_PROVIDER_NAME and settings.anthropic_fable_routing_enabled
        # Accounts with vendor-side extra usage keep answering 200 after the
        # primary window exhausts and silently bill metered credits, so the
        # window itself is a hard gate. Token counting is quota-free upstream
        # and never bills, so its dedicated quota key stays exempt.
        extra_usage_gate = provider_name == ANTHROPIC_PROVIDER_NAME and quota_key != _count_tokens_quota_key(
            provider_name
        )
        async with self._repo_factory() as repos:
            provider_accounts = [
                account for account in await repos.accounts.list_accounts() if account.provider.lower() == provider_name
            ]
            # Scope eligibility to the same routable pool the load balancer
            # uses. Canceled-subscription, deactivated, paused, and
            # reauth-required rows can never be selected, so counting them
            # as "remaining candidates" produces misleading diagnostics and
            # masks the real "all usable accounts are cooling down" state.
            account_ids = [account.id for account in selectable_accounts(provider_accounts)]
            request_quota_latest = await repos.additional_usage.latest_by_account(
                quota_key,
                _ANTHROPIC_COOLDOWN_WINDOW,
                account_ids=account_ids,
            )
            request_quota_cooldowns = {
                account_id: (float(entry.used_percent), entry.reset_at)
                for account_id, entry in request_quota_latest.items()
            }
            extra_usage_tripwire_cooldowns: dict[str, tuple[float, int | None, int]] = {}
            if extra_usage_gate:
                extra_usage_tripwire_latest = await repos.additional_usage.latest_by_account(
                    _ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
                    _ANTHROPIC_COOLDOWN_WINDOW,
                    account_ids=account_ids,
                )
                extra_usage_tripwire_cooldowns = {
                    account_id: (
                        float(entry.used_percent),
                        entry.reset_at,
                        naive_utc_to_epoch(entry.recorded_at),
                    )
                    for account_id, entry in extra_usage_tripwire_latest.items()
                }
            # A persisted primary window with reset_at=None must not gate an
            # account out of routing indefinitely. Only a bounded, still-future
            # reset counts as an active exhaustion here; a None reset re-admits
            # the account (a genuine 429 re-writes a bounded cooldown).
            primary_exhaustion: dict[str, int] = {}
            primary_headroom_recorded_at: dict[str, int] = {}
            credit_enabled_exhausted_account_ids: frozenset[str] = frozenset()
            if extra_usage_gate:
                primary_usage = await repos.usage.latest_by_account(window="primary", account_ids=account_ids)
                primary_headroom_recorded_at = {
                    account_id: naive_utc_to_epoch(entry.recorded_at)
                    for account_id, entry in primary_usage.items()
                    if entry.used_percent is not None and float(entry.used_percent) < 100.0
                }
                primary_exhaustion = {
                    account_id: int(entry.reset_at)
                    for account_id, entry in primary_usage.items()
                    if entry.used_percent is not None
                    and float(entry.used_percent) >= 100.0
                    and entry.reset_at is not None
                    and int(entry.reset_at) > now
                }
                credit_enabled_exhausted_account_ids = frozenset(
                    account_id
                    for account_id, entry in primary_usage.items()
                    if account_id in primary_exhaustion
                    and (
                        entry.credits_unlimited is True
                        or (
                            entry.credits_has is True
                            and entry.credits_balance is not None
                            and float(entry.credits_balance) > 0.0
                        )
                    )
                )
            weekly_usage_used_percent: dict[str, float] = {}
            secondary_exhaustion: dict[str, int | None] = {}
            if extra_usage_gate or fable_routing:
                secondary_usage = await repos.usage.latest_by_account(window="secondary", account_ids=account_ids)
                weekly_usage_used_percent = {
                    account_id: float(entry.used_percent) for account_id, entry in secondary_usage.items()
                }
                secondary_exhaustion = {
                    account_id: int(entry.reset_at) if entry.reset_at is not None else None
                    for account_id, entry in secondary_usage.items()
                    if entry.used_percent is not None
                    and float(entry.used_percent) >= 100.0
                    and (entry.reset_at is None or int(entry.reset_at) > now)
                }
            fable_access_markers: dict[str, tuple[float, int | None]] = {}
            fable_scoped_markers: dict[str, tuple[float, int | None, int]] = {}
            if fable_routing:
                # Snapshot scalars inside the repo session — the ORM rows
                # detach once the context closes (regression 1f47e633).
                fable_access = await repos.additional_usage.latest_by_account(
                    _ANTHROPIC_FABLE_ACCESS_QUOTA_KEY,
                    _ANTHROPIC_FABLE_ACCESS_WINDOW,
                    account_ids=account_ids,
                )
                fable_access_markers = {
                    account_id: (float(entry.used_percent), entry.reset_at)
                    for account_id, entry in fable_access.items()
                }
                fable_scoped = await repos.additional_usage.latest_by_account(
                    _ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
                    _ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW,
                    account_ids=account_ids,
                )
                # recorded_at is a naive-UTC datetime column; snapshot it as
                # an epoch int here too, alongside the other scalars, since
                # the ORM row detaches once this session context closes.
                fable_scoped_markers = {
                    account_id: (
                        float(entry.used_percent),
                        entry.reset_at,
                        naive_utc_to_epoch(entry.recorded_at),
                    )
                    for account_id, entry in fable_scoped.items()
                }

        fable_request = fable_routing and _is_fable_model(model)
        threshold = settings.anthropic_fable_weekly_max_used_percent if fable_routing else 0.0
        scoped_threshold = settings.anthropic_fable_scoped_max_used_percent if fable_routing else 0.0

        def _weekly_used(account_id: str) -> float:
            return weekly_usage_used_percent.get(account_id, 0.0)

        def _fresh_scoped_percent(account_id: str) -> float | None:
            # Anthropic's dedicated Fable-scoped weekly percent is
            # authoritative when fresh. The caller additionally checks that
            # an exhausted marker has a still-future reset before treating it
            # as a hard model-scope exclusion.
            marker = fable_scoped_markers.get(account_id)
            if marker is None:
                return None
            scoped_used_percent, _scoped_reset_at, recorded_epoch = marker
            if now - recorded_epoch >= _FABLE_SCOPED_FRESH_SECONDS:
                return None
            return scoped_used_percent

        def _has_fresh_capable_fable_marker(account_id: str) -> bool:
            marker = fable_access_markers.get(account_id)
            if marker is None:
                return False
            marker_used_percent, marker_reset_at = marker
            if marker_used_percent >= 100.0:
                return False
            return marker_reset_at is None or marker_reset_at > now

        model_scope_account_ids = account_ids
        preferred_fable_account_ids: frozenset[str] = frozenset()
        hard_excluded_fable_account_ids: set[str] = set()
        hard_excluded_fable_reset_by_account_id: dict[str, int] = {}
        if fable_request:
            model_scope_account_ids = []
            preferred_ids: set[str] = set()
            for account_id in account_ids:
                scoped_percent = _fresh_scoped_percent(account_id)
                scoped_marker = fable_scoped_markers.get(account_id)
                scoped_reset_at = scoped_marker[1] if scoped_marker is not None else None
                if (
                    scoped_percent is not None
                    and scoped_percent >= scoped_threshold
                    and scoped_reset_at is not None
                    and int(scoped_reset_at) > now
                ):
                    hard_excluded_fable_account_ids.add(account_id)
                    hard_excluded_fable_reset_by_account_id[account_id] = int(scoped_reset_at)
                    continue
                model_scope_account_ids.append(account_id)
                if (
                    scoped_percent is not None
                    or _weekly_used(account_id) < threshold
                    or _has_fresh_capable_fable_marker(account_id)
                ):
                    preferred_ids.add(account_id)
            preferred_fable_account_ids = frozenset(preferred_ids)

        eligible_account_ids: list[str] = []
        blocked_reset_by_account_id: dict[str, int] = {}
        blocked_count = 0
        request_quota_blocked_account_ids: set[str] = set()

        def _active_extra_usage_tripwire_blocks(account_id: str) -> bool:
            tripwire = extra_usage_tripwire_cooldowns.get(account_id)
            if tripwire is None or not _anthropic_cooldown_is_active(tripwire[0], tripwire[1], now=now):
                return False
            recovered_at = primary_headroom_recorded_at.get(account_id)
            return recovered_at is None or recovered_at <= tripwire[2]

        for account_id in model_scope_account_ids:
            request_quota_cooldown = request_quota_cooldowns.get(account_id)
            if request_quota_cooldown is not None and _anthropic_cooldown_is_active(
                request_quota_cooldown[0], request_quota_cooldown[1], now=now
            ):
                blocked_count += 1
                request_quota_blocked_account_ids.add(account_id)
                if request_quota_cooldown[1] is not None:
                    blocked_reset_by_account_id[account_id] = int(request_quota_cooldown[1])
                continue
            primary_reset_at = primary_exhaustion.get(account_id)
            secondary_is_exhausted = account_id in secondary_exhaustion
            if primary_reset_at is not None or secondary_is_exhausted:
                blocked_count += 1
                if secondary_is_exhausted:
                    secondary_reset_at = secondary_exhaustion[account_id]
                    # An account cannot serve subscription or paid traffic
                    # until every active quota window is available. Unknown
                    # weekly reset time means there is no bounded retry time
                    # for this account, even when primary has a known reset.
                    if secondary_reset_at is not None:
                        blocked_reset_by_account_id[account_id] = max(
                            reset_at
                            for reset_at in (primary_reset_at, secondary_reset_at)
                            if reset_at is not None
                        )
                elif primary_reset_at is not None:
                    blocked_reset_by_account_id[account_id] = primary_reset_at
                continue
            tripwire_cooldown = extra_usage_tripwire_cooldowns.get(account_id)
            if tripwire_cooldown is not None and _active_extra_usage_tripwire_blocks(account_id):
                blocked_count += 1
                if tripwire_cooldown[1] is not None:
                    blocked_reset_by_account_id[account_id] = int(tripwire_cooldown[1])
                continue
            eligible_account_ids.append(account_id)

        if fable_request and eligible_account_ids:
            preferred_standard_account_ids = [
                account_id for account_id in eligible_account_ids if account_id in preferred_fable_account_ids
            ]
            if preferred_standard_account_ids:
                eligible_account_ids = preferred_standard_account_ids
            else:
                # The heuristic is a preference, not a model-capability oracle.
                # Healthy primary headroom in the soft Fable pool must be used
                # before paid extra usage is considered.
                logger.warning(
                    "All subscription-covered Anthropic accounts are soft Fable fallbacks "
                    "past the weekly threshold (%.0f%%); using that headroom model=%s",
                    threshold,
                    model,
                )

        pool_primary_exhausted = bool(model_scope_account_ids) and all(
            account_id in primary_exhaustion for account_id in model_scope_account_ids
        )
        paid_fallback_account_ids: frozenset[str] = frozenset()
        if not eligible_account_ids and pool_primary_exhausted and settings.anthropic_route_to_extra_usage:
            # Opt-in last resort: only pool-wide primary-window exhaustion can
            # authorize paid traffic. A healthy-primary account blocked by a
            # model/quota cooldown must not cause another account to spend
            # credits. Re-admit only exhausted accounts whose same latest
            # primary snapshot reports usable extra-usage capacity; this also
            # recognizes the dedicated response tripwire without weakening
            # real request-quota cooldowns written by upstream 429 responses.
            paid_candidates = [
                account_id
                for account_id in model_scope_account_ids
                if account_id in credit_enabled_exhausted_account_ids
                and account_id not in request_quota_blocked_account_ids
                and account_id not in secondary_exhaustion
            ]
            if fable_request:
                preferred_paid_candidates = [
                    account_id for account_id in paid_candidates if account_id in preferred_fable_account_ids
                ]
                if preferred_paid_candidates:
                    paid_candidates = preferred_paid_candidates
            eligible_account_ids = paid_candidates
            paid_fallback_account_ids = frozenset(paid_candidates)
            blocked_count -= len(eligible_account_ids)
            for account_id in eligible_account_ids:
                blocked_reset_by_account_id.pop(account_id, None)

        if not eligible_account_ids and request_quota_blocked_account_ids:
            # Last resort for transient rate-limit cooldowns: when every
            # remaining candidate is blocked only by a requested-quota
            # cooldown that resets within the near-term horizon, re-admit
            # those candidates and let upstream be the authority — a genuine
            # limit answers 429 and rewrites a fresh bounded cooldown. All
            # transient candidates are re-admitted (not just the earliest
            # reset) so the caller's failover loop can exclude an account
            # that just 429'd and still try the next one. Accounts with
            # active primary/secondary exhaustion, an extra-usage tripwire,
            # or a reset beyond the horizon stay blocked.
            horizon = now + _ANTHROPIC_TRANSIENT_COOLDOWN_BYPASS_HORIZON_SECONDS

            def _is_transient_cooldown_only(account_id: str) -> bool:
                if account_id in primary_exhaustion or account_id in secondary_exhaustion:
                    return False
                if _active_extra_usage_tripwire_blocks(account_id):
                    return False
                cooldown = request_quota_cooldowns.get(account_id)
                if cooldown is None or cooldown[1] is None:
                    return False
                return int(cooldown[1]) <= horizon

            transient_account_ids = [
                account_id
                for account_id in model_scope_account_ids
                if account_id in request_quota_blocked_account_ids and _is_transient_cooldown_only(account_id)
            ]
            if transient_account_ids:
                logger.warning(
                    "anthropic_transient_cooldown_bypass quota_key=%s readmitted=%d blocked=%d",
                    quota_key,
                    len(transient_account_ids),
                    len(request_quota_blocked_account_ids),
                )
                eligible_account_ids = transient_account_ids
                blocked_count -= len(transient_account_ids)
                for account_id in transient_account_ids:
                    blocked_reset_by_account_id.pop(account_id, None)

        burn_first_account_ids: frozenset[str] = frozenset()
        if fable_routing and not fable_request and eligible_account_ids:
            def _is_over_fable_threshold(account_id: str) -> bool:
                scoped_percent = _fresh_scoped_percent(account_id)
                if scoped_percent is not None:
                    return scoped_percent >= scoped_threshold
                return _weekly_used(account_id) >= threshold

            burn_first_account_ids = frozenset(
                account_id
                for account_id in eligible_account_ids
                if _is_over_fable_threshold(account_id)
            )

        if not eligible_account_ids and hard_excluded_fable_account_ids:
            # Hard Fable exclusions remain outside model scope and therefore
            # outside paid-pool exhaustion. They still explain a terminal
            # no-route result and contribute their earliest known reset.
            blocked_count += len(hard_excluded_fable_account_ids)
            blocked_reset_by_account_id.update(hard_excluded_fable_reset_by_account_id)

        return _AnthropicQuotaEligibility(
            account_ids=eligible_account_ids,
            blocked_count=blocked_count,
            next_reset_at=min(blocked_reset_by_account_id.values()) if blocked_reset_by_account_id else None,
            paid_fallback_account_ids=paid_fallback_account_ids,
            burn_first_account_ids=burn_first_account_ids,
        )

    async def _record_quota_cooldown(self, account: Account, *, quota_key: str, error: UpstreamError) -> None:
        reset_at = _reset_at_from_rate_limit_error(error)
        now = int(time.time())
        window_minutes = max(1, int((reset_at - now + 59) / 60)) if reset_at is not None else None
        try:
            async with self._repo_factory() as repos:
                await repos.additional_usage.add_entry(
                    account.id,
                    limit_name=quota_key,
                    metered_feature=_ANTHROPIC_COOLDOWN_FEATURE,
                    quota_key=quota_key,
                    window=_ANTHROPIC_COOLDOWN_WINDOW,
                    used_percent=100.0,
                    reset_at=reset_at,
                    window_minutes=window_minutes,
                )
        except Exception:
            logger.warning(
                "Failed to persist Anthropic quota cooldown account_id=%s quota_key=%s",
                account.id,
                quota_key,
                exc_info=True,
            )

    async def _clear_quota_cooldown(self, account: Account, *, quota_key: str) -> None:
        try:
            async with self._repo_factory() as repos:
                await repos.additional_usage.add_entry(
                    account.id,
                    limit_name=quota_key,
                    metered_feature=_ANTHROPIC_COOLDOWN_FEATURE,
                    quota_key=quota_key,
                    window=_ANTHROPIC_COOLDOWN_WINDOW,
                    used_percent=0.0,
                    reset_at=None,
                    window_minutes=None,
                )
        except Exception:
            logger.warning(
                "Failed to clear Anthropic quota cooldown account_id=%s quota_key=%s",
                account.id,
                quota_key,
                exc_info=True,
            )

    async def _fresh_access_token(self, account: Account) -> str:
        try:
            async with self._repo_factory() as repos:
                latest = await repos.accounts.get_by_id(account.id)
                if latest is None:
                    raise AnthropicProxyError(503, "Selected Anthropic account no longer exists")
                manager = AuthManager(repos.accounts)
                fresh = await manager.ensure_fresh(latest)
                return self._encryptor.decrypt(fresh.access_token_encrypted)
        except RefreshError as exc:
            if exc.is_permanent or classify_refresh_error(exc.code):
                exc.is_permanent = True
                await self._load_balancer.mark_permanent_failure(account, exc.code)
            raise AnthropicProxyError(401, exc.message, code=exc.code) from exc

    async def _persist_request_log(
        self,
        *,
        account: Account | None,
        provider_name: str,
        request_id: str,
        model: str,
        started_at: float,
        status: str,
        api_key: ApiKeyData | None,
        session_id: str | None,
        useragent: str | None,
        useragent_group: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: AnthropicUsage | None = None,
    ) -> None:
        try:
            async with self._repo_factory() as repos:
                await repos.request_logs.add_log(
                    account_id=account.id if account else None,
                    api_key_id=api_key.id if api_key else None,
                    request_id=request_id,
                    model=model,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    cached_input_tokens=usage.cache_read_input_tokens if usage else None,
                    cache_creation_tokens=usage.cache_creation_input_tokens if usage else None,
                    cache_read_tokens=usage.cache_read_input_tokens if usage else None,
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    plan_type=account.plan_type if account else None,
                    provider=provider_name,
                    transport="http",
                    session_id=session_id,
                    useragent=useragent,
                    useragent_group=useragent_group,
                )
        except Exception:
            logger.warning("Failed to persist Anthropic request log request_id=%s", request_id, exc_info=True)

    async def _finalize_api_key_reservation(
        self,
        reservation: ApiKeyUsageReservationData | None,
        *,
        model: str,
        usage: AnthropicUsage | None,
    ) -> None:
        if reservation is None:
            return
        if usage is None:
            await self._release_api_key_reservation(reservation)
            return
        async with self._repo_factory() as repos:
            service = ApiKeysService(repos.api_keys)
            cached_tokens = (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cached_input_tokens=cached_tokens,
            )

    async def _release_api_key_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None:
            return
        async with self._repo_factory() as repos:
            service = ApiKeysService(repos.api_keys)
            await service.release_usage_reservation(reservation.reservation_id)

    async def _openrouter_credit_eligibility(self) -> _AnthropicQuotaEligibility:
        async with self._repo_factory() as repos:
            provider_accounts = [
                account
                for account in await repos.accounts.list_accounts()
                if account.provider.lower() == OPENROUTER_PROVIDER_NAME
            ]
            selectable = selectable_accounts(provider_accounts)
            account_ids = [account.id for account in selectable]
            credits_usage = await repos.usage.latest_by_account(
                window=CREDITS_USAGE_WINDOW,
                account_ids=account_ids,
            )
        eligible: list[str] = []
        blocked = 0
        for account in selectable:
            if account.status == AccountStatus.QUOTA_EXCEEDED:
                blocked += 1
                continue
            window = window_from_usage(credits_usage.get(account.id))
            if window is not None and credits_exhausted(window):
                blocked += 1
                continue
            eligible.append(account.id)
        return _AnthropicQuotaEligibility(account_ids=eligible, blocked_count=blocked)

    async def claim_session_route(
        self,
        *,
        session_id: str,
        model: str,
        quota_key: str,
        affinity_quota_key: str | None = None,
        provider_name: str = ANTHROPIC_PROVIDER_NAME,
    ) -> Account:
        sticky_quota_key = affinity_quota_key or quota_key
        sticky_key = f"{_sticky_prefix(provider_name)}:{sticky_quota_key}:session:{_hash_for_key(session_id)}"
        account = await self._select_account(
            model,
            provider_name=provider_name,
            exclude_account_ids=set(),
            sticky_key=sticky_key,
            quota_key=quota_key,
        )
        async with self._repo_factory() as repos:
            await repos.sticky_sessions.upsert(sticky_key, account.id, kind=StickySessionKind.CODEX_SESSION)
        return account


def _request_id_from_headers(headers: Mapping[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("request-id")


def _anthropic_quota_key(payload: AnthropicMessageRequest) -> str:
    model = payload.model.lower()
    if "haiku" in model:
        return "anthropic_standard"
    if payload.thinking:
        return "anthropic_top_thinking"
    return "anthropic_top"


def _is_fable_model(model: str | None) -> bool:
    return bool(model) and "fable" in model.lower()


def _messages_provider_name(payload: AnthropicMessageRequest) -> str:
    return _provider_name_for_model(payload.model)


def _provider_name_for_model(model: str) -> str:
    normalized = model.strip().lower()
    if normalized.startswith("glm-"):
        return GLM_PROVIDER_NAME
    definition = get_additional_quota_definition_for_model(model)
    if definition is not None and definition.quota_key == OPENROUTER_CREDITS_QUOTA_KEY:
        return OPENROUTER_PROVIDER_NAME
    return ANTHROPIC_PROVIDER_NAME


def _count_tokens_quota_key(provider_name: str) -> str:
    # Dedicated key with no recorded cooldowns: count_tokens is quota-free
    # upstream, so message-quota cooldowns must not exclude accounts from it.
    return "glm_count_tokens" if provider_name == GLM_PROVIDER_NAME else "anthropic_count_tokens"


def _upstream_base_url(provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return get_settings().glm_anthropic_upstream_base_url
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return OPENROUTER_UPSTREAM_BASE_URL
    return get_settings().anthropic_upstream_base_url


def _messages_quota_key(payload: AnthropicMessageRequest, *, provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "glm_coding_thinking" if payload.thinking else "glm_coding"
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return OPENROUTER_CREDITS_QUOTA_KEY
    if _anthropic_fast_mode_requested(payload):
        return _ANTHROPIC_FAST_QUOTA_KEY
    return _anthropic_quota_key(payload)


def _messages_affinity_quota_key(payload: AnthropicMessageRequest, *, provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "glm_coding_thinking" if payload.thinking else "glm_coding"
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return OPENROUTER_CREDITS_QUOTA_KEY
    base = _anthropic_quota_key(payload)
    # Fable-class traffic gets its own affinity family so a session that
    # interleaves Fable and non-Fable requests holds two independent sticky
    # pins instead of ping-ponging one pin between an under-threshold (Fable)
    # and an over-threshold (burn_first) account on every model switch.
    return f"{base}_fable" if _is_fable_model(payload.model) else base


def _anthropic_fast_mode_requested(payload: AnthropicMessageRequest) -> bool:
    speed = payload.speed
    return isinstance(speed, str) and speed.strip().lower() == "fast"


def _messages_sticky_key(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
    *,
    provider_name: str,
    quota_key: str,
) -> str | None:
    header_value = _anthropic_session_header(headers)
    prefix = _sticky_prefix(provider_name)
    if header_value:
        return f"{prefix}:{quota_key}:session:{_hash_for_key(header_value)}"
    derived = _derive_anthropic_session_material(payload)
    if not derived:
        return None
    return f"{prefix}:{quota_key}:derived:{_hash_for_key(derived)}"


def _anthropic_sticky_key(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
    *,
    quota_key: str,
) -> str | None:
    header_value = _anthropic_session_header(headers)
    if header_value:
        return f"claude:{quota_key}:session:{_hash_for_key(header_value)}"
    derived = _derive_anthropic_session_material(payload)
    if not derived:
        return None
    return f"claude:{quota_key}:derived:{_hash_for_key(derived)}"


def _sticky_prefix(provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "glm"
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return "openrouter"
    return "claude"


def _provider_label(provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "GLM"
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return "OpenRouter"
    return "Anthropic"


def _no_available_accounts_code(provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "no_available_glm_accounts"
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return "no_available_openrouter_accounts"
    return "no_available_anthropic_accounts"


def _quota_cooldown_code(provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "glm_quota_cooldown"
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return "openrouter_quota_cooldown"
    return "anthropic_quota_cooldown"


def _other_provider_routing_message(provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "OpenAI and Anthropic accounts are not eligible for GLM routing."
    if provider_name == OPENROUTER_PROVIDER_NAME:
        return "Subscription accounts are not eligible for OpenRouter routing."
    return "OpenAI accounts are not eligible for Claude routing."


def _anthropic_request_session_id(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
) -> str | None:
    user_id = payload.metadata.get("user_id") if payload.metadata is not None else None
    if isinstance(user_id, str):
        try:
            decoded = json.loads(user_id)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            session_id = decoded.get("session_id")
            if isinstance(session_id, str) and session_id.strip():
                return session_id.strip()

        legacy_match = re.search(
            r"_session_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
            user_id.strip(),
        )
        if legacy_match is not None:
            return legacy_match.group(1)

    return _anthropic_session_header(headers)


def _anthropic_session_header(headers: Mapping[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for key in (
        "x-claude-session-id",
        "x-claude-conversation-id",
        "x-claude-code-session-id",
        "session_id",
        "x-codex-session-id",
        "x-codex-conversation-id",
        "x-codex-turn-state",
    ):
        value = normalized.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _derive_anthropic_session_material(payload: AnthropicMessageRequest) -> str | None:
    parts: list[str] = [payload.model.lower()]
    system_text = _anthropic_content_text(payload.system)
    if system_text:
        parts.append(system_text[:512])
    for message in payload.messages:
        if message.role == "user":
            user_text = _anthropic_content_text(message.content)
            if user_text:
                parts.append(user_text[:512])
            break
    if len(parts) == 1:
        return None
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _anthropic_content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        if isinstance(item, Mapping):
            value = item.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts) if parts else None


def _account_status_label(account: Account) -> str:
    status = account.status
    return status.value if hasattr(status, "value") else str(status)


def _anthropic_selection_quota_detail(
    *,
    quota_key: str,
    blocked_count: int,
    candidate_count: int | None,
    selection_error_code: str | None,
    selection_error_message: str | None,
    retry_at: int | None,
) -> str | None:
    parts: list[str] = []
    if blocked_count > 0:
        noun = "account" if blocked_count == 1 else "accounts"
        reset_text = f" until {datetime.fromtimestamp(retry_at).isoformat()}" if retry_at is not None else ""
        parts.append(f"{quota_key} cooldown excluded {blocked_count} {noun}{reset_text}")
    if candidate_count is not None and (blocked_count > 0 or selection_error_code):
        noun = "account" if candidate_count == 1 else "accounts"
        parts.append(f"{candidate_count} {noun} remained after the {quota_key} prefilter")
    if selection_error_code:
        reason = selection_error_code
        if selection_error_message and selection_error_message != "No available accounts":
            reason = f"{selection_error_message} ({selection_error_code})"
        parts.append(f"selector reason: {reason}")
    return "; ".join(parts) if parts else None


def _hash_for_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:32]


def _anthropic_cooldown_is_active(used_percent: float, reset_at: int | None, *, now: int) -> bool:
    if float(used_percent) < 100.0:
        return False
    # A persisted exhaustion with no reset horizon must not block routing
    # forever. Without a bounded reset we cannot know the window is still
    # exhausted, so re-admit the account; if it is genuinely exhausted the
    # next upstream 429 trips a fresh bounded cooldown.
    if reset_at is None:
        return False
    return int(reset_at) > now


def _build_anthropic_headers(
    inbound_headers: Mapping[str, str],
    access_token: str,
    *,
    provider_name: str = ANTHROPIC_PROVIDER_NAME,
    fast_mode: bool = False,
) -> dict[str, str]:
    settings = get_settings()
    headers = {
        key: value
        for key, value in filter_inbound_headers(inbound_headers).items()
        if key.lower() not in {"api-key", "x-api-key"}
    }
    lower_keys = {key.lower() for key in headers}
    headers["Authorization"] = f"Bearer {access_token}"
    if "anthropic-version" not in lower_keys:
        headers["anthropic-version"] = settings.anthropic_version
    if "content-type" not in lower_keys:
        headers["content-type"] = "application/json"
    if "accept" not in lower_keys:
        headers["accept"] = "text/event-stream"
    if provider_name == ANTHROPIC_PROVIDER_NAME:
        required_betas = [_ANTHROPIC_OAUTH_BETA]
        if fast_mode:
            required_betas.append(_ANTHROPIC_FAST_MODE_BETA)
        _merge_anthropic_beta_header(headers, required_betas)
    return headers


def _merge_anthropic_beta_header(headers: dict[str, str], required_betas: list[str]) -> None:
    beta_key = next((key for key in headers if key.lower() == "anthropic-beta"), "anthropic-beta")
    existing = headers.get(beta_key, "")
    merged: list[str] = []
    seen: set[str] = set()
    for raw_value in [*existing.split(","), *required_betas]:
        beta = raw_value.strip()
        if not beta:
            continue
        normalized = beta.lower()
        if normalized in seen:
            continue
        merged.append(beta)
        seen.add(normalized)
    if merged:
        headers[beta_key] = ", ".join(merged)


def _collect_usage_from_chunk(
    text_buffer: str,
    chunk: bytes,
    usage: AnthropicUsage | None,
) -> tuple[str, AnthropicUsage | None, AnthropicErrorEvent | None]:
    """Parse forwarded SSE chunks for usage and in-band error events.

    Anthropic reports mid-stream failures as an SSE ``error`` event on an
    HTTP 200 stream; without inspecting the forwarded bytes those failures
    are invisible to account health and request logging.
    """
    error_event: AnthropicErrorEvent | None = None
    text_buffer += chunk.decode("utf-8", errors="replace")
    normalized = text_buffer.replace("\r\n", "\n")
    while "\n\n" in normalized:
        block, normalized = normalized.split("\n\n", 1)
        event = parse_sse_event(block)
        if event is None:
            continue
        if isinstance(event, AnthropicErrorEvent):
            error_event = event
            continue
        event_usage = getattr(event, "usage", None)
        if event_usage is None and getattr(event, "message", None) is not None:
            event_usage = event.message.usage
        usage = merge_usage_values(usage, event_usage)
    return normalized, usage, error_event


def _usage_from_json_body(raw: bytes) -> AnthropicUsage | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    usage_data = payload.get("usage")
    if not isinstance(usage_data, dict):
        return None
    try:
        return AnthropicUsage.model_validate(usage_data)
    except ValidationError:
        return None


async def _read_error_message(resp: aiohttp.ClientResponse) -> str:
    return (await _read_error_details(resp)).message


async def _read_error_details(resp: aiohttp.ClientResponse) -> _AnthropicErrorDetails:
    raw = await resp.read()
    if not raw:
        return _AnthropicErrorDetails(f"Anthropic upstream returned HTTP {resp.status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _AnthropicErrorDetails(raw.decode("utf-8", errors="replace"))
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            error_type = error.get("type")
            if isinstance(message, str):
                return _AnthropicErrorDetails(
                    message,
                    error_type if isinstance(error_type, str) else None,
                )
        if isinstance(payload.get("message"), str):
            error_type = payload.get("type")
            return _AnthropicErrorDetails(
                payload["message"],
                error_type if isinstance(error_type, str) else None,
            )
    return _AnthropicErrorDetails(f"Anthropic upstream returned HTTP {resp.status}")


def _anthropic_error_type_for_status(status_code: int) -> str:
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 529:
        return "overloaded_error"
    if status_code == 504:
        return "timeout_error"
    if status_code >= 500:
        return "api_error"
    if status_code in {401, 403}:
        return "authentication_error"
    if status_code == 404:
        return "not_found_error"
    return "invalid_request_error"


def _pool_wait_should_hold(
    exc: AnthropicProxyError,
    *,
    wait_enabled: bool,
    deadline: float | None,
) -> bool:
    if not wait_enabled or deadline is None:
        return False
    # Only pool-wide exhaustion with a known earliest reset is worth holding
    # for; selection failures without reset timing are not reset-recoverable.
    if exc.retry_at is None:
        return False
    return _POOL_WAIT_CLOCK() < deadline


async def _hold_for_pool_reset(error: AnthropicProxyError, *, deadline: float | None) -> None:
    if deadline is None:
        raise error
    now = _POOL_WAIT_CLOCK()
    remaining = deadline - now
    if remaining <= 0:
        raise error
    delay = _pool_wait_delay_seconds(error.retry_at, now=now, remaining=remaining)
    logger.info(
        "Anthropic pool exhausted; holding stream open for reset retry_at=%s delay_seconds=%.1f",
        error.retry_at,
        delay,
    )
    if delay > 0:
        await _POOL_WAIT_SLEEP(delay)


def _pool_wait_delay_seconds(retry_at: int | None, *, now: float, remaining: float) -> float:
    base = float(retry_at) - now if retry_at is not None else _POOL_WAIT_MIN_POLL_SECONDS
    base = min(max(base, _POOL_WAIT_MIN_POLL_SECONDS), _POOL_WAIT_MAX_POLL_SECONDS)
    return max(0.0, min(base + _POOL_WAIT_JITTER(), remaining))


def _extra_usage_tripwire_error(headers: Mapping[str, str]) -> UpstreamError | None:
    if not _unified_limit_exhausted(headers):
        return None
    error = UpstreamError(message="Anthropic unified limit exhausted; upstream is billing extra-usage credits")
    reset_at = _parse_reset_epoch(_get_header(headers, "anthropic-ratelimit-unified-reset"))
    if reset_at is None:
        reset_at = _parse_reset_epoch(_get_header(headers, "anthropic-ratelimit-unified-5h-reset"))
    if reset_at is not None:
        error["resets_at"] = reset_at
    return error


def _unified_limit_exhausted(headers: Mapping[str, str]) -> bool:
    overage_in_use = _get_header(headers, _ANTHROPIC_UNIFIED_OVERAGE_IN_USE_HEADER)
    if isinstance(overage_in_use, str) and overage_in_use.strip().lower() == "true":
        return True
    for name in _ANTHROPIC_UNIFIED_STATUS_HEADERS:
        value = _get_header(headers, name)
        if value is None:
            continue
        normalized = value.strip().lower()
        # On a 2xx, any non-allowed unified status means the subscription
        # window rejected the request yet upstream served it anyway — i.e.
        # it billed credits. Unknown statuses fail closed: a spurious
        # cooldown is bounded, silent credit burn is not.
        if normalized and normalized not in _ANTHROPIC_UNIFIED_ALLOWED_STATUSES:
            return True
    return False


def _rate_limit_error_from_response(resp: aiohttp.ClientResponse, message: str) -> UpstreamError:
    error = UpstreamError(message=message)
    reset_at = _anthropic_rate_limit_reset_at(resp.headers)
    if reset_at is not None:
        error["resets_at"] = reset_at
    retry_after = _retry_after_seconds(resp.headers)
    if retry_after is not None:
        error["resets_in_seconds"] = retry_after
    return error


def _reset_at_from_rate_limit_error(error: UpstreamError) -> int:
    reset_at = error.get("resets_at")
    if isinstance(reset_at, int | float):
        return int(reset_at)
    reset_in_seconds = error.get("resets_in_seconds")
    if isinstance(reset_in_seconds, int | float):
        return int(time.time() + max(0, reset_in_seconds))
    return int(time.time() + _ANTHROPIC_DEFAULT_COOLDOWN_SECONDS)


def _anthropic_rate_limit_reset_at(headers: Mapping[str, str]) -> int | None:
    reset_candidates = [
        "anthropic-ratelimit-unified-reset",
        "anthropic-ratelimit-unified-overage-reset",
        "anthropic-ratelimit-requests-reset",
        "anthropic-ratelimit-tokens-reset",
        "anthropic-ratelimit-input-tokens-reset",
        "anthropic-ratelimit-output-tokens-reset",
        "anthropic-priority-input-tokens-reset",
        "anthropic-priority-output-tokens-reset",
        "anthropic-fast-input-tokens-reset",
        "anthropic-fast-output-tokens-reset",
    ]
    epochs = [_parse_reset_epoch(_get_header(headers, name)) for name in reset_candidates]
    future_epochs = [epoch for epoch in epochs if epoch is not None and epoch > int(time.time())]
    if future_epochs:
        return min(future_epochs)
    past_epochs = [epoch for epoch in epochs if epoch is not None]
    return max(past_epochs) if past_epochs else None


def _retry_after_seconds(headers: Mapping[str, str]) -> int | None:
    retry_after = _get_header(headers, "retry-after")
    if retry_after is None:
        return None
    try:
        return max(0, int(float(retry_after)))
    except ValueError:
        return None


def _parse_reset_epoch(value: str | None) -> int | None:
    """Parse a reset header as RFC 3339 or Unix epoch (seconds or milliseconds)."""
    if not value:
        return None
    normalized = value.strip()
    try:
        epoch = float(normalized)
    except ValueError:
        pass
    else:
        if epoch <= 0:
            return None
        if epoch > 1e12:  # milliseconds
            epoch /= 1000.0
        return int(epoch)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return value
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None
