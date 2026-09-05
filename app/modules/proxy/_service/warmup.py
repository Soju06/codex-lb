from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import uuid4

import aiohttp

from app.core.auth.refresh import (
    RefreshError,
    is_transient_refresh_contention,
    refresh_contention_kind,
)
from app.core.balancer.logic import ACCOUNT_USAGE_LIMIT_REACHED_ERROR_CODE, ACCOUNT_USAGE_LIMIT_REACHED_ERROR_MESSAGE
from app.core.clients.proxy import ProxyResponseError, UpstreamProxyRouteTrace, filter_inbound_headers
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import ProxyAuthError, ProxyRateLimitError
from app.core.openai.models import CompactResponsePayload
from app.core.openai.requests import ResponsesCompactRequest
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.usage.account_limits import AccountUsageLimitState, evaluate_standard_usage_limit
from app.core.usage.types import UsageWindowRow
from app.db.models import Account, AccountStatus
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy._service.support import _call_with_supported_optional_kwargs, _request_log_client_fields
from app.modules.proxy.helpers import _header_account_id, _normalize_error_code, _parse_openai_error
from app.modules.proxy.request_policy import (
    apply_prohibit_fast_mode,
    normalize_upstream_model_alias,
    validate_model_access,
)
from app.modules.usage.authorization import OwnerAuthorization, OwnerAuthorizationKind, load_owner_authorization
from app.modules.usage.mappers import usage_history_to_window_row

logger = logging.getLogger(__name__)

_REQUEST_TRANSPORT_HTTP = "http"
_WARMUP_MODES = frozenset({"normal", "strict", "force"})
_WARMUP_SKIP_INELIGIBLE_PRIMARY = "ineligible_primary_usage"
_WARMUP_USAGE_LIMIT_AUTHORIZATION_FAILED = "account_usage_limit_authorization_failed"
_WARMUP_MAX_CONCURRENT_SUBMISSIONS = 5
_CompactResponses = Callable[
    [ResponsesCompactRequest, Mapping[str, str], str, str | None],
    Awaitable[CompactResponsePayload],
]


class _WarmupServiceProtocol(Protocol):
    _encryptor: Any
    _load_balancer: Any
    _repo_factory: Callable[[], Any]

    async def _ensure_fresh_with_budget(self, account: Account, *, timeout_seconds: float) -> Account: ...

    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None: ...

    async def _resolve_upstream_route_for_account(self, account: Account, *, operation: str) -> Any: ...

    async def _write_request_log(self, **kwargs: Any) -> None: ...

    async def _release_websocket_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None: ...


def _service_core_compact_responses() -> _CompactResponses:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(_CompactResponses, getattr(service_module, "core_compact_responses", core_compact_responses))
    return core_compact_responses


@dataclass(frozen=True, slots=True)
class WarmupSubmittedAccountData:
    account_id: str
    request_id: str
    model: str


@dataclass(frozen=True, slots=True)
class WarmupSkippedAccountData:
    account_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class WarmupFailedAccountData:
    account_id: str
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class WarmupExecutionData:
    mode: str
    total_accounts: int
    submitted: list[WarmupSubmittedAccountData]
    skipped: list[WarmupSkippedAccountData]
    failed: list[WarmupFailedAccountData]


@dataclass(frozen=True, slots=True)
class _WarmupSubmitResult:
    success: bool
    request_id: str
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _WarmupUsageSnapshot:
    primary: UsageWindowRow | None = None
    secondary: UsageWindowRow | None = None
    monthly: UsageWindowRow | None = None


@dataclass(frozen=True, slots=True)
class _WarmupAccountSnapshot:
    id: str
    chatgpt_account_id: str | None
    email: str
    plan_type: str
    access_token_encrypted: bytes
    refresh_token_encrypted: bytes
    id_token_encrypted: bytes
    last_refresh: datetime
    status: AccountStatus
    deactivation_reason: str | None
    reset_at: int | None
    blocked_at: int | None
    usage_limit_enabled: bool
    usage_limit_percent: float | None


@dataclass(frozen=True, slots=True)
class _WarmupAuthorization:
    account: _WarmupAccountSnapshot | None
    decision: OwnerAuthorization


def _is_warmup_usage_eligible(entry: UsageWindowRow | None) -> bool:
    if entry is None:
        return False
    if entry.window_minutes != 300:
        return False
    if entry.used_percent is None:
        return False
    return float(entry.used_percent) <= 0.0


def _evaluate_warmup_usage_limit(
    account: _WarmupAccountSnapshot,
    usage: _WarmupUsageSnapshot,
) -> AccountUsageLimitState:
    return evaluate_standard_usage_limit(
        enabled=account.usage_limit_enabled,
        limit_percent=account.usage_limit_percent,
        plan_type=account.plan_type,
        primary=usage.primary,
        secondary=usage.secondary,
        monthly=usage.monthly,
        refresh_interval_seconds=get_settings().usage_refresh_interval_seconds,
    )


def _snapshot_warmup_account(account: Account) -> _WarmupAccountSnapshot:
    return _WarmupAccountSnapshot(
        id=account.id,
        chatgpt_account_id=account.chatgpt_account_id,
        email=account.email,
        plan_type=account.plan_type,
        access_token_encrypted=account.access_token_encrypted,
        refresh_token_encrypted=account.refresh_token_encrypted,
        id_token_encrypted=account.id_token_encrypted,
        last_refresh=account.last_refresh,
        status=account.status,
        deactivation_reason=account.deactivation_reason,
        reset_at=account.reset_at,
        blocked_at=account.blocked_at,
        usage_limit_enabled=bool(account.usage_limit_enabled),
        usage_limit_percent=account.usage_limit_percent,
    )


def _materialize_warmup_account(account: _WarmupAccountSnapshot) -> Account:
    return Account(
        id=account.id,
        chatgpt_account_id=account.chatgpt_account_id,
        email=account.email,
        plan_type=account.plan_type,
        access_token_encrypted=account.access_token_encrypted,
        refresh_token_encrypted=account.refresh_token_encrypted,
        id_token_encrypted=account.id_token_encrypted,
        last_refresh=account.last_refresh,
        status=account.status,
        deactivation_reason=account.deactivation_reason,
        reset_at=account.reset_at,
        blocked_at=account.blocked_at,
        usage_limit_enabled=account.usage_limit_enabled,
        usage_limit_percent=account.usage_limit_percent,
    )


class _WarmupMixin:
    async def warmup(
        self,
        *,
        mode: str,
        headers: Mapping[str, str],
        api_key: ApiKeyData | None = None,
    ) -> WarmupExecutionData:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in _WARMUP_MODES:
            raise ValueError(f"Unsupported warmup mode: {mode}")

        proxy = cast(_WarmupServiceProtocol, self)
        async with proxy._repo_factory() as repos:
            all_accounts = await repos.accounts.list_accounts()
            target_accounts = self._resolve_warmup_target_accounts(
                [_snapshot_warmup_account(account) for account in all_accounts],
                api_key=api_key,
            )
            target_account_ids = [account.id for account in target_accounts]
            latest_primary = await repos.usage.latest_by_account(
                window="primary",
                account_ids=target_account_ids,
            )
            latest_secondary = await repos.usage.latest_by_account(
                window="secondary",
                account_ids=target_account_ids,
            )
            latest_monthly = await repos.usage.latest_by_account(
                window="monthly",
                account_ids=target_account_ids,
            )
            standard_usage_by_account = {
                account.id: _WarmupUsageSnapshot(
                    primary=(
                        usage_history_to_window_row(latest_primary[account.id])
                        if account.id in latest_primary
                        else None
                    ),
                    secondary=(
                        usage_history_to_window_row(latest_secondary[account.id])
                        if account.id in latest_secondary
                        else None
                    ),
                    monthly=(
                        usage_history_to_window_row(latest_monthly[account.id])
                        if account.id in latest_monthly
                        else None
                    ),
                )
                for account in target_accounts
            }

        total_accounts = len(target_accounts)
        submitted: list[WarmupSubmittedAccountData] = []
        skipped: list[WarmupSkippedAccountData] = []
        failed: list[WarmupFailedAccountData] = []

        if total_accounts == 0:
            return WarmupExecutionData(
                mode=normalized_mode,
                total_accounts=0,
                submitted=submitted,
                skipped=skipped,
                failed=failed,
            )

        usage_limit_states = {
            account.id: _evaluate_warmup_usage_limit(account, standard_usage_by_account[account.id])
            for account in target_accounts
        }
        policy_eligible_accounts = [
            account for account in target_accounts if not usage_limit_states[account.id].blocks_account_use
        ]
        eligible_accounts = [
            account
            for account in policy_eligible_accounts
            if _is_warmup_usage_eligible(standard_usage_by_account[account.id].primary)
        ]
        policy_eligible_ids = {account.id for account in policy_eligible_accounts}
        skipped.extend(
            WarmupSkippedAccountData(
                account_id=account.id,
                reason=ACCOUNT_USAGE_LIMIT_REACHED_ERROR_CODE,
            )
            for account in target_accounts
            if account.id not in policy_eligible_ids
        )

        if normalized_mode == "force":
            accounts_to_submit = policy_eligible_accounts
        elif normalized_mode == "strict":
            if len(eligible_accounts) != len(target_accounts):
                raise ValueError("strict warmup requires every target account to be usage-eligible")
            accounts_to_submit = target_accounts
        else:
            eligible_ids = {account.id for account in eligible_accounts}
            accounts_to_submit = eligible_accounts
            skipped.extend(
                WarmupSkippedAccountData(
                    account_id=account.id,
                    reason=_WARMUP_SKIP_INELIGIBLE_PRIMARY,
                )
                for account in policy_eligible_accounts
                if account.id not in eligible_ids
            )

        dashboard_settings = await get_settings_cache().get()
        configured_model = dashboard_settings.warmup_model
        prohibit_fast_mode = dashboard_settings.prohibit_fast_mode
        effective_model = api_key.enforced_model if api_key and api_key.enforced_model else configured_model
        validate_model_access(api_key, effective_model)
        filtered_headers = filter_inbound_headers(headers)

        submission_semaphore = asyncio.Semaphore(_WARMUP_MAX_CONCURRENT_SUBMISSIONS)

        async def _submit_account_warmup(account: _WarmupAccountSnapshot) -> _WarmupSubmitResult:
            async with submission_semaphore:
                return await self._submit_warmup_request(
                    account=account,
                    api_key=api_key,
                    headers=filtered_headers,
                    warmup_model=effective_model,
                    prohibit_fast_mode=prohibit_fast_mode,
                )

        submission_results = await asyncio.gather(*(_submit_account_warmup(account) for account in accounts_to_submit))

        for index, result in enumerate(submission_results):
            account = accounts_to_submit[index]
            if result.success:
                submitted.append(
                    WarmupSubmittedAccountData(
                        account_id=account.id,
                        request_id=result.request_id,
                        model=effective_model,
                    )
                )
            else:
                failed.append(
                    WarmupFailedAccountData(
                        account_id=account.id,
                        error_code=result.error_code or "upstream_error",
                        error_message=result.error_message or "Warmup request failed",
                    )
                )

        return WarmupExecutionData(
            mode=normalized_mode,
            total_accounts=total_accounts,
            submitted=submitted,
            skipped=skipped,
            failed=failed,
        )

    def _resolve_warmup_target_accounts(
        self,
        accounts: list[_WarmupAccountSnapshot],
        *,
        api_key: ApiKeyData | None,
    ) -> list[_WarmupAccountSnapshot]:
        active_accounts = [
            account for account in accounts if account.status in (AccountStatus.ACTIVE, AccountStatus.REAUTH_REQUIRED)
        ]
        if api_key is None or not api_key.account_assignment_scope_enabled:
            return active_accounts
        assigned_ids = {account_id for account_id in api_key.assigned_account_ids if account_id}
        return [account for account in active_accounts if account.id in assigned_ids]

    async def _submit_warmup_request(
        self,
        *,
        account: _WarmupAccountSnapshot,
        api_key: ApiKeyData | None,
        headers: Mapping[str, str],
        warmup_model: str,
        prohibit_fast_mode: bool,
    ) -> _WarmupSubmitResult:
        started_at = time.monotonic()
        useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
        live_account = _materialize_warmup_account(account)
        request_id = str(uuid4())
        upstream_headers = {
            key: value for key, value in headers.items() if key.lower() not in {"x-request-id", "request-id"}
        }
        upstream_headers["x-request-id"] = request_id
        status = "error"
        error_code: str | None = None
        error_message: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        cached_input_tokens: int | None = None
        reasoning_tokens: int | None = None
        reservation: ApiKeyUsageReservationData | None = None
        upstream_proxy_route_mode: str | None = None
        upstream_proxy_pool_id: str | None = None
        upstream_proxy_endpoint_id: str | None = None
        upstream_proxy_fallback_used: bool | None = None
        upstream_proxy_fail_closed_reason: str | None = None
        proxy = cast(_WarmupServiceProtocol, self)

        try:
            refresh_timeout = max(1.0, float(get_settings().upstream_connect_timeout_seconds))
            live_account = await proxy._ensure_fresh_with_budget(live_account, timeout_seconds=refresh_timeout)
            route = await proxy._resolve_upstream_route_for_account(live_account, operation="warmup")
            if route is not None:
                upstream_proxy_route_mode = route.mode
                upstream_proxy_pool_id = route.pool_id
                upstream_proxy_endpoint_id = route.endpoint_id
            try:
                authorization = await self._load_fresh_warmup_authorization(live_account.id)
            except Exception:
                logger.warning(
                    "Final public warmup usage authorization failed closed",
                    extra={"account_id": live_account.id, "request_id": request_id},
                    exc_info=True,
                )
                error_code = _WARMUP_USAGE_LIMIT_AUTHORIZATION_FAILED
                error_message = "Account usage-limit authorization failed"
                return _WarmupSubmitResult(
                    success=False,
                    request_id=request_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            if authorization.decision.kind is OwnerAuthorizationKind.AUTHORIZATION_FAILED:
                error_code = _WARMUP_USAGE_LIMIT_AUTHORIZATION_FAILED
                error_message = "Account usage-limit authorization failed"
                return _WarmupSubmitResult(
                    success=False,
                    request_id=request_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            if authorization.account is None:
                error_code = "account_not_found"
                error_message = "Account no longer exists"
                return _WarmupSubmitResult(
                    success=False,
                    request_id=request_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            if authorization.decision.kind is OwnerAuthorizationKind.OWNER_UNAVAILABLE:
                error_code = "account_not_active"
                error_message = f"Account status is {authorization.account.status.value}"
                return _WarmupSubmitResult(
                    success=False,
                    request_id=request_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            if authorization.decision.kind is OwnerAuthorizationKind.USAGE_POLICY_BLOCKED:
                error_code = ACCOUNT_USAGE_LIMIT_REACHED_ERROR_CODE
                error_message = ACCOUNT_USAGE_LIMIT_REACHED_ERROR_MESSAGE
                return _WarmupSubmitResult(
                    success=False,
                    request_id=request_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            live_account = _materialize_warmup_account(authorization.account)
            access_token = proxy._encryptor.decrypt(live_account.access_token_encrypted)
            account_header_id = _header_account_id(live_account.chatgpt_account_id)
            route_trace = UpstreamProxyRouteTrace()
            payload = ResponsesCompactRequest(
                model=warmup_model,
                instructions="Warmup request.",
                input="warmup",
                store=False,
            )
            normalize_upstream_model_alias(payload)
            apply_prohibit_fast_mode(
                payload,
                prohibit_fast_mode=prohibit_fast_mode,
                request_id=request_id,
            )
            response = await _call_with_supported_optional_kwargs(
                _service_core_compact_responses(),
                payload,
                upstream_headers,
                access_token,
                account_header_id,
                optional_kwargs={
                    "route": route,
                    "allow_direct_egress": route is None,
                    "route_trace": route_trace,
                    "chatgpt_account_id": account_header_id,
                },
            )
            if route_trace.mode is not None:
                upstream_proxy_route_mode = route_trace.mode
                upstream_proxy_pool_id = route_trace.pool_id
                upstream_proxy_endpoint_id = route_trace.endpoint_id
                upstream_proxy_fallback_used = route_trace.fallback_used
            await proxy._load_balancer.record_success(live_account)
            status = "success"
            request_id = response.id or request_id
            usage = response.usage
            input_tokens = usage.input_tokens if usage else None
            output_tokens = usage.output_tokens if usage else None
            cached_input_tokens = (
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            )
            reasoning_tokens = (
                usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
            )
        except RefreshError as exc:
            if exc.is_permanent:
                await proxy._load_balancer.mark_permanent_failure(live_account, exc.code)
                error_code = "invalid_api_key"
            elif is_transient_refresh_contention(exc):
                # Transient CROSS-REPLICA refresh contention. This covers BOTH
                # benign claim contention (``refresh_claim_timeout``: a peer
                # replica held the account's refresh claim past the wait budget)
                # AND a post-exchange guarded-write CAS conflict
                # (``token_persist_conflict`` / ``status_downgrade_conflict``).
                # In every case the account's OAuth credentials are healthy, so it
                # MUST surface as a retryable ``upstream_unavailable`` (matching
                # the five core proxy request paths) rather than a bogus
                # ``invalid_api_key`` that presents a healthy account as an auth
                # failure in the warmup result and request log. No health penalty
                # is applied (mirroring those paths). A post-exchange persist
                # conflict is the rarer, more-serious race, so it is logged
                # distinctly from benign contention.
                if refresh_contention_kind(exc) == "persist_conflict":
                    logger.warning(
                        "Warmup refresh post-exchange persist conflict code=%s request_id=%s account_id=%s",
                        exc.code,
                        request_id,
                        live_account.id,
                    )
                error_code = "upstream_unavailable"
            else:
                # A genuine OAuth transport failure (``code == "transport_error"``)
                # or any other non-permanent refresh failure keeps the prior
                # ``invalid_api_key`` classification.
                error_code = "invalid_api_key"
            error_message = exc.message
        except UpstreamProxyRouteError as exc:
            upstream_proxy_fail_closed_reason = exc.reason
            error_code = "upstream_proxy_unavailable"
            error_message = str(exc) or "Warmup upstream proxy route is unavailable"
        except ProxyResponseError as exc:
            await proxy._handle_proxy_error(live_account, exc)
            error = _parse_openai_error(exc.payload)
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            error_message = error.message if error else "Warmup request failed"
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            error_code = "upstream_unavailable"
            error_message = str(exc) or "Request to upstream timed out"
        except ProxyAuthError as exc:
            error_code = "auth_error"
            error_message = str(exc) or "Warmup authentication failed"
        except ProxyRateLimitError as exc:
            error_code = "rate_limit_exceeded"
            error_message = str(exc) or "Warmup request was rate limited"
        except Exception as exc:
            error_code = "upstream_error"
            error_message = str(exc) or "Warmup request failed"
        finally:
            try:
                await proxy._write_request_log(
                    account_id=account.id,
                    api_key=api_key,
                    request_id=request_id,
                    model=warmup_model,
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    reasoning_tokens=reasoning_tokens,
                    transport=_REQUEST_TRANSPORT_HTTP,
                    request_kind="warmup",
                    upstream_proxy_route_mode=upstream_proxy_route_mode,
                    upstream_proxy_pool_id=upstream_proxy_pool_id,
                    upstream_proxy_endpoint_id=upstream_proxy_endpoint_id,
                    upstream_proxy_fallback_used=upstream_proxy_fallback_used,
                    upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
                    useragent=useragent,
                    useragent_group=useragent_group,
                    conversation_id=conversation_id,
                )
            finally:
                await proxy._release_websocket_reservation(reservation)

        if status == "success":
            return _WarmupSubmitResult(success=True, request_id=request_id)
        return _WarmupSubmitResult(
            success=False,
            request_id=request_id,
            error_code=error_code or "upstream_error",
            error_message=error_message or "Warmup request failed",
        )

    async def _load_fresh_warmup_authorization(self, account_id: str) -> _WarmupAuthorization:
        proxy = cast(_WarmupServiceProtocol, self)
        async with proxy._repo_factory() as repos:
            account = await repos.accounts.get_by_id_fresh(account_id)
            if account is None or account.status != AccountStatus.ACTIVE:
                return _WarmupAuthorization(
                    account=_snapshot_warmup_account(account) if account is not None else None,
                    decision=OwnerAuthorization(
                        OwnerAuthorizationKind.OWNER_UNAVAILABLE,
                        owner_status=account.status if account is not None else None,
                    ),
                )
            decision = await load_owner_authorization(
                repos.usage,
                account_id,
                refresh_interval_seconds=get_settings().usage_refresh_interval_seconds,
                require_active=True,
            )
            snapshot = decision.snapshot
            if snapshot is None:
                return _WarmupAuthorization(account=None, decision=decision)
            account_snapshot = replace(
                _snapshot_warmup_account(account),
                status=snapshot.status,
                plan_type=snapshot.plan_type,
                usage_limit_enabled=snapshot.enabled,
                usage_limit_percent=snapshot.limit_percent,
            )
            return _WarmupAuthorization(account=account_snapshot, decision=decision)
