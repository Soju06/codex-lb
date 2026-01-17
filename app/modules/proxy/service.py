from __future__ import annotations

import logging
from collections.abc import Sequence
from hashlib import sha256
import time
from datetime import timedelta
from typing import AsyncIterator, Iterable, Mapping

import anyio
from pydantic import ValidationError

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError
from app.core.config.settings import get_settings
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.clients.proxy import stream_responses as core_stream_responses
from app.core.crypto import TokenEncryptor
from app.core.errors import OpenAIErrorDetail, OpenAIErrorEnvelope, openai_error, response_failed_event
from app.core.openai.models import OpenAIError, OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.usage.types import UsageWindowRow, UsageWindowSummary
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import format_sse_event
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.auth_manager import AuthManager
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.proxy.types import (
    CreditStatusDetailsData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.proxy.usage_updater import UsageUpdater
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


class ProxyService:
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        usage_repo: UsageRepository,
        logs_repo: RequestLogsRepository,
        sticky_repo: StickySessionsRepository,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._usage_repo = usage_repo
        self._logs_repo = logs_repo
        self._encryptor = TokenEncryptor()
        self._auth_manager = AuthManager(accounts_repo)
        self._load_balancer = LoadBalancer(accounts_repo, usage_repo, sticky_repo)
        self._usage_updater = UsageUpdater(usage_repo, accounts_repo)

    def stream_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        propagate_http_errors: bool = False,
    ) -> AsyncIterator[str]:
        _maybe_log_proxy_request_shape("stream", payload, headers)
        filtered = filter_inbound_headers(headers)
        return self._stream_with_retry(
            payload,
            filtered,
            propagate_http_errors=propagate_http_errors,
        )

    async def compact_responses(
        self,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> OpenAIResponsePayload:
        _maybe_log_proxy_request_shape("compact", payload, headers)
        filtered = filter_inbound_headers(headers)
        sticky_key = _sticky_key_from_compact_payload(payload)
        selection = await self._load_balancer.select_account(
            sticky_key=sticky_key,
            reallocate_sticky=sticky_key is not None,
        )
        account = selection.account
        if not account:
            raise ProxyResponseError(
                503,
                openai_error("no_accounts", selection.error_message or "No active accounts available"),
            )
        account = await self._ensure_fresh(account)
        account_id = _header_account_id(account.id)

        async def _call_compact(target: Account) -> OpenAIResponsePayload:
            access_token = self._encryptor.decrypt(target.access_token_encrypted)
            return await core_compact_responses(payload, filtered, access_token, account_id)

        try:
            return await _call_compact(account)
        except ProxyResponseError as exc:
            if exc.status_code != 401:
                await self._handle_proxy_error(account, exc)
                raise
            try:
                account = await self._ensure_fresh(account, force=True)
            except RefreshError as refresh_exc:
                if refresh_exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                raise exc
            try:
                return await _call_compact(account)
            except ProxyResponseError as exc:
                await self._handle_proxy_error(account, exc)
                raise

    async def rate_limit_headers(self) -> dict[str, str]:
        now = utcnow()
        accounts = await self._accounts_repo.list_accounts()
        account_map = {account.id: account for account in accounts}

        headers: dict[str, str] = {}
        primary_minutes = await self._usage_repo.latest_window_minutes("primary")
        if primary_minutes is None:
            primary_minutes = usage_core.default_window_minutes("primary")
        if primary_minutes:
            primary_rows = await self._usage_repo.aggregate_since(
                now - timedelta(minutes=primary_minutes),
                window="primary",
            )
            if primary_rows:
                summary = usage_core.summarize_usage_window(
                    [row.to_window_row() for row in primary_rows],
                    account_map,
                    "primary",
                )
                headers.update(_rate_limit_headers("primary", summary))

        secondary_minutes = await self._usage_repo.latest_window_minutes("secondary")
        if secondary_minutes is None:
            secondary_minutes = usage_core.default_window_minutes("secondary")
        if secondary_minutes:
            secondary_rows = await self._usage_repo.aggregate_since(
                now - timedelta(minutes=secondary_minutes),
                window="secondary",
            )
            if secondary_rows:
                summary = usage_core.summarize_usage_window(
                    [row.to_window_row() for row in secondary_rows],
                    account_map,
                    "secondary",
                )
                headers.update(_rate_limit_headers("secondary", summary))

        latest_usage = await self._usage_repo.latest_by_account()
        headers.update(_credits_headers(latest_usage.values()))
        return headers

    async def get_rate_limit_payload(self) -> RateLimitStatusPayloadData:
        accounts = await self._accounts_repo.list_accounts()
        await self._refresh_usage(accounts)
        selected_accounts = _select_accounts_for_limits(accounts)
        if not selected_accounts:
            return RateLimitStatusPayloadData(plan_type="guest")

        account_map = {account.id: account for account in selected_accounts}
        primary_rows = await self._latest_usage_rows(account_map, "primary")
        secondary_rows = await self._latest_usage_rows(account_map, "secondary")

        primary_summary = _summarize_window(primary_rows, account_map, "primary")
        secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")

        now_epoch = int(time.time())
        primary_window = _window_snapshot(primary_summary, primary_rows, "primary", now_epoch)
        secondary_window = _window_snapshot(secondary_summary, secondary_rows, "secondary", now_epoch)

        return RateLimitStatusPayloadData(
            plan_type=_plan_type_for_accounts(selected_accounts),
            rate_limit=_rate_limit_details(primary_window, secondary_window),
            credits=_credits_snapshot(await self._latest_usage_entries(account_map)),
        )

    async def _stream_with_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        propagate_http_errors: bool,
    ) -> AsyncIterator[str]:
        request_id = ensure_request_id()
        sticky_key = _sticky_key_from_payload(payload)
        max_attempts = 3
        for attempt in range(max_attempts):
            selection = await self._load_balancer.select_account(sticky_key=sticky_key)
            account = selection.account
            if not account:
                event = response_failed_event(
                    "no_accounts",
                    selection.error_message or "No active accounts available",
                    response_id=request_id,
                )
                yield format_sse_event(event)
                return

            account_id_value = account.id
            try:
                account = await self._ensure_fresh(account)
                async for line in self._stream_once(
                    account,
                    payload,
                    headers,
                    request_id,
                    attempt < max_attempts - 1,
                ):
                    yield line
                return
            except _RetryableStreamError as exc:
                await self._handle_stream_error(account, exc.error, exc.code)
                continue
            except ProxyResponseError as exc:
                if exc.status_code == 401:
                    try:
                        account = await self._ensure_fresh(account, force=True)
                    except RefreshError as refresh_exc:
                        if refresh_exc.is_permanent:
                            await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                        continue
                    async for line in self._stream_once(account, payload, headers, request_id, False):
                        yield line
                    return
                error = _parse_openai_error(exc.payload)
                error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                error_message = error.message if error else None
                error_type = error.type if error else None
                error_param = error.param if error else None
                await self._handle_stream_error(
                    account,
                    _upstream_error_from_openai(error),
                    error_code,
                )
                if propagate_http_errors:
                    raise
                event = response_failed_event(
                    error_code,
                    error_message or "Upstream error",
                    error_type=error_type or "server_error",
                    response_id=request_id,
                    error_param=error_param,
                )
                _apply_error_metadata(event["response"]["error"], error)
                yield format_sse_event(event)
                return
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                continue
            except Exception:
                try:
                    await self._load_balancer.record_error(account)
                except Exception:
                    logger.warning(
                        "Failed to record proxy error account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )
                if attempt == max_attempts - 1:
                    event = response_failed_event(
                        "upstream_error",
                        "Proxy streaming failed",
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    return
        event = response_failed_event(
            "no_accounts",
            "No available accounts after retries",
            response_id=request_id,
        )
        yield format_sse_event(event)

    async def _stream_once(
        self,
        account: Account,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        request_id: str,
        allow_retry: bool,
    ) -> AsyncIterator[str]:
        account_id_value = account.id
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        account_id = _header_account_id(account_id_value)
        model = payload.model
        reasoning_effort = payload.reasoning.effort if payload.reasoning else None
        start = time.monotonic()
        status = "success"
        error_code = None
        error_message = None
        usage = None

        try:
            stream = core_stream_responses(
                payload,
                headers,
                access_token,
                account_id,
                raise_for_status=True,
            )
            iterator = stream.__aiter__()
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                return
            event = parse_sse_event(first)
            if event and event.type in ("response.failed", "error"):
                error = event.response.error if event.type == "response.failed" else event.error
                code = _normalize_error_code(
                    error.code if error else None,
                    error.type if error else None,
                )
                status = "error"
                error_code = code
                error_message = error.message if error else None
                if allow_retry:
                    error_payload = _upstream_error_from_openai(error)
                    raise _RetryableStreamError(code, error_payload)

            if event and event.type == "response.completed":
                usage = event.response.usage if event.response else None
            yield first

            async for line in iterator:
                event = parse_sse_event(line)
                if event:
                    event_type = event.type
                    if event_type in ("response.failed", "error"):
                        status = "error"
                        error = event.response.error if event_type == "response.failed" else event.error
                        error_code = _normalize_error_code(
                            error.code if error else None,
                            error.type if error else None,
                        )
                        error_message = error.message if error else None
                    if event_type == "response.completed":
                        usage = event.response.usage if event.response else None
                yield line
        except ProxyResponseError as exc:
            error = _parse_openai_error(exc.payload)
            status = "error"
            error_code = _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            error_message = error.message if error else None
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            input_tokens = usage.input_tokens if usage else None
            output_tokens = usage.output_tokens if usage else None
            cached_input_tokens = (
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            )
            reasoning_tokens = (
                usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
            )
            with anyio.CancelScope(shield=True):
                try:
                    await self._logs_repo.add_log(
                        account_id=account_id_value,
                        request_id=request_id,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        reasoning_tokens=reasoning_tokens,
                        reasoning_effort=reasoning_effort,
                        latency_ms=latency_ms,
                        status=status,
                        error_code=error_code,
                        error_message=error_message,
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist request log account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )

    async def _refresh_usage(self, accounts: list[Account]) -> None:
        latest_usage = await self._usage_repo.latest_by_account(window="primary")
        await self._usage_updater.refresh_accounts(accounts, latest_usage)

    async def _latest_usage_rows(
        self,
        account_map: dict[str, Account],
        window: str,
    ) -> list[UsageWindowRow]:
        if not account_map:
            return []
        latest = await self._usage_repo.latest_by_account(window=window)
        return [
            UsageWindowRow(
                account_id=entry.account_id,
                used_percent=entry.used_percent,
                reset_at=entry.reset_at,
                window_minutes=entry.window_minutes,
            )
            for entry in latest.values()
            if entry.account_id in account_map
        ]

    async def _latest_usage_entries(
        self,
        account_map: dict[str, Account],
    ) -> list[UsageHistory]:
        if not account_map:
            return []
        latest = await self._usage_repo.latest_by_account()
        return [entry for entry in latest.values() if entry.account_id in account_map]

    async def _ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        return await self._auth_manager.ensure_fresh(account, force=force)

    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None:
        error = _parse_openai_error(exc.payload)
        code = _normalize_error_code(
            error.code if error else None,
            error.type if error else None,
        )
        await self._handle_stream_error(account, _upstream_error_from_openai(error), code)

    async def _handle_stream_error(self, account: Account, error: UpstreamError, code: str) -> None:
        if code in {"rate_limit_exceeded", "usage_limit_reached"}:
            await self._load_balancer.mark_rate_limit(account, error)
            return
        if code in {"insufficient_quota", "usage_not_included", "quota_exceeded"}:
            await self._load_balancer.mark_quota_exceeded(account, error)
            return
        if code in PERMANENT_FAILURE_CODES:
            await self._load_balancer.mark_permanent_failure(account, code)
            return
        await self._load_balancer.record_error(account)


def _header_account_id(account_id: str | None) -> str | None:
    if not account_id:
        return None
    if account_id.startswith(("email_", "local_")):
        return None
    return account_id


KNOWN_PLAN_TYPES = {
    "guest",
    "free",
    "go",
    "plus",
    "pro",
    "free_workspace",
    "team",
    "business",
    "education",
    "quorum",
    "k12",
    "enterprise",
    "edu",
}

PLAN_TYPE_PRIORITY = (
    "enterprise",
    "business",
    "team",
    "pro",
    "plus",
    "education",
    "edu",
    "free_workspace",
    "free",
    "go",
    "guest",
    "quorum",
    "k12",
)


def _select_accounts_for_limits(accounts: Iterable[Account]) -> list[Account]:
    return [account for account in accounts if account.status not in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED)]


def _summarize_window(
    rows: list[UsageWindowRow],
    account_map: dict[str, Account],
    window: str,
) -> UsageWindowSummary | None:
    if not rows:
        return None
    return usage_core.summarize_usage_window(rows, account_map, window)


def _window_snapshot(
    summary: UsageWindowSummary | None,
    rows: list[UsageWindowRow],
    window: str,
    now_epoch: int,
) -> RateLimitWindowSnapshotData | None:
    if summary is None:
        return None

    used_percent = _normalize_used_percent(summary.used_percent, rows)
    if used_percent is None:
        return None

    reset_at = summary.reset_at
    if reset_at is None:
        return None

    window_minutes = summary.window_minutes or usage_core.default_window_minutes(window)
    if not window_minutes:
        return None

    limit_window_seconds = int(window_minutes * 60)
    reset_after_seconds = max(0, int(reset_at) - now_epoch)

    return RateLimitWindowSnapshotData(
        used_percent=_percent_to_int(used_percent),
        limit_window_seconds=limit_window_seconds,
        reset_after_seconds=reset_after_seconds,
        reset_at=int(reset_at),
    )


def _normalize_used_percent(
    value: float | None,
    rows: Iterable[UsageWindowRow],
) -> float | None:
    if value is not None:
        return value
    values = [row.used_percent for row in rows if row.used_percent is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _percent_to_int(value: float) -> int:
    bounded = max(0.0, min(100.0, value))
    return int(bounded)


def _rate_limit_details(
    primary: RateLimitWindowSnapshotData | None,
    secondary: RateLimitWindowSnapshotData | None,
) -> RateLimitStatusDetailsData | None:
    if not primary and not secondary:
        return None
    used_percents = [window.used_percent for window in (primary, secondary) if window]
    limit_reached = any(used >= 100 for used in used_percents)
    return RateLimitStatusDetailsData(
        allowed=not limit_reached,
        limit_reached=limit_reached,
        primary_window=primary,
        secondary_window=secondary,
    )


def _aggregate_credits(entries: Iterable[UsageHistory]) -> tuple[bool, bool, float] | None:
    has_data = False
    has_credits = False
    unlimited = False
    balance_total = 0.0

    for entry in entries:
        credits_has = entry.credits_has
        credits_unlimited = entry.credits_unlimited
        credits_balance = entry.credits_balance
        if credits_has is None and credits_unlimited is None and credits_balance is None:
            continue
        has_data = True
        if credits_has is True:
            has_credits = True
        if credits_unlimited is True:
            unlimited = True
        if credits_balance is not None and not credits_unlimited:
            try:
                balance_total += float(credits_balance)
            except (TypeError, ValueError):
                continue

    if not has_data:
        return None
    if unlimited:
        has_credits = True
    return has_credits, unlimited, balance_total


def _credits_snapshot(entries: Iterable[UsageHistory]) -> CreditStatusDetailsData | None:
    aggregate = _aggregate_credits(entries)
    if aggregate is None:
        return None
    has_credits, unlimited, balance_total = aggregate
    balance_value = str(round(balance_total, 2))
    return CreditStatusDetailsData(
        has_credits=has_credits,
        unlimited=unlimited,
        balance=balance_value,
        approx_local_messages=None,
        approx_cloud_messages=None,
    )


def _plan_type_for_accounts(accounts: Iterable[Account]) -> str:
    normalized = [_normalize_plan_type(account.plan_type) for account in accounts]
    filtered = [plan for plan in normalized if plan is not None]
    if not filtered:
        return "guest"
    unique = set(filtered)
    if len(unique) == 1:
        return filtered[0]
    for plan in PLAN_TYPE_PRIORITY:
        if plan in unique:
            return plan
    return "guest"


def _normalize_plan_type(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized not in KNOWN_PLAN_TYPES:
        return None
    return normalized


def _rate_limit_headers(
    window_label: str,
    summary: UsageWindowSummary,
) -> dict[str, str]:
    used_percent = summary.used_percent
    window_minutes = summary.window_minutes
    if used_percent is None or window_minutes is None:
        return {}
    headers = {
        f"x-codex-{window_label}-used-percent": str(float(used_percent)),
        f"x-codex-{window_label}-window-minutes": str(int(window_minutes)),
    }
    reset_at = summary.reset_at
    if reset_at is not None:
        headers[f"x-codex-{window_label}-reset-at"] = str(int(reset_at))
    return headers


def _credits_headers(entries: Iterable[UsageHistory]) -> dict[str, str]:
    aggregate = _aggregate_credits(entries)
    if aggregate is None:
        return {}
    has_credits, unlimited, balance_total = aggregate
    balance_value = f"{balance_total:.2f}"
    return {
        "x-codex-credits-has-credits": "true" if has_credits else "false",
        "x-codex-credits-unlimited": "true" if unlimited else "false",
        "x-codex-credits-balance": balance_value,
    }


def _normalize_error_code(code: str | None, error_type: str | None) -> str:
    value = code or error_type
    if not value:
        return "upstream_error"
    return value.lower()


def _parse_openai_error(payload: OpenAIErrorEnvelope) -> OpenAIError | None:
    error = payload.get("error")
    if not error:
        return None
    try:
        return OpenAIError.model_validate(error)
    except ValidationError:
        if not isinstance(error, dict):
            return None
        return OpenAIError(
            message=_coerce_str(error.get("message")),
            type=_coerce_str(error.get("type")),
            code=_coerce_str(error.get("code")),
            param=_coerce_str(error.get("param")),
            plan_type=_coerce_str(error.get("plan_type")),
            resets_at=_coerce_number(error.get("resets_at")),
            resets_in_seconds=_coerce_number(error.get("resets_in_seconds")),
        )


def _coerce_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_number(value: object) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _apply_error_metadata(target: OpenAIErrorDetail, error: OpenAIError | None) -> None:
    if not error:
        return
    if error.plan_type is not None:
        target["plan_type"] = error.plan_type
    if error.resets_at is not None:
        target["resets_at"] = error.resets_at
    if error.resets_in_seconds is not None:
        target["resets_in_seconds"] = error.resets_in_seconds


class _RetryableStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


def _upstream_error_from_openai(error: OpenAIError | None) -> UpstreamError:
    if not error:
        return {}
    data = error.model_dump(exclude_none=True)
    payload: UpstreamError = {}
    message = data.get("message")
    if isinstance(message, str):
        payload["message"] = message
    resets_at = data.get("resets_at")
    if isinstance(resets_at, (int, float)):
        payload["resets_at"] = resets_at
    resets_in_seconds = data.get("resets_in_seconds")
    if isinstance(resets_in_seconds, (int, float)):
        payload["resets_in_seconds"] = resets_in_seconds
    return payload


def _maybe_log_proxy_request_shape(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
) -> None:
    settings = get_settings()
    if not settings.log_proxy_request_shape:
        return

    request_id = get_request_id()
    prompt_cache_key = getattr(payload, "prompt_cache_key", None)
    if prompt_cache_key is None and payload.model_extra:
        extra_value = payload.model_extra.get("prompt_cache_key")
        if isinstance(extra_value, str):
            prompt_cache_key = extra_value
    prompt_cache_key_hash = _hash_identifier(prompt_cache_key) if isinstance(prompt_cache_key, str) else None
    prompt_cache_key_raw = (
        _truncate_identifier(prompt_cache_key)
        if settings.log_proxy_request_shape_raw_cache_key and isinstance(prompt_cache_key, str)
        else None
    )

    extra_keys = sorted(payload.model_extra.keys()) if payload.model_extra else []
    fields_set = sorted(payload.model_fields_set)
    input_summary = _summarize_input(payload.input)
    header_keys = _interesting_header_keys(headers)

    logger.warning(
        "proxy_request_shape request_id=%s kind=%s model=%s stream=%s input=%s prompt_cache_key=%s prompt_cache_key_raw=%s fields=%s extra=%s headers=%s",
        request_id,
        kind,
        payload.model,
        getattr(payload, "stream", None),
        input_summary,
        prompt_cache_key_hash,
        prompt_cache_key_raw,
        fields_set,
        extra_keys,
        header_keys,
    )


def _hash_identifier(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _summarize_input(items: Sequence[object]) -> str:
    if not items:
        return "0"
    type_counts: dict[str, int] = {}
    for item in items:
        type_name = type(item).__name__
        type_counts[type_name] = type_counts.get(type_name, 0) + 1
    summary = ",".join(f"{key}={type_counts[key]}" for key in sorted(type_counts))
    return f"{len(items)}({summary})"


def _truncate_identifier(value: str, *, max_length: int = 96) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:48]}...{value[-16:]}"


def _interesting_header_keys(headers: Mapping[str, str]) -> list[str]:
    allowlist = {
        "user-agent",
        "x-request-id",
        "request-id",
        "x-openai-client-id",
        "x-openai-client-version",
        "x-openai-client-arch",
        "x-openai-client-os",
        "x-openai-client-user-agent",
        "x-codex-session-id",
        "x-codex-conversation-id",
    }
    return sorted({key.lower() for key in headers.keys() if key.lower() in allowlist})


def _sticky_key_from_payload(payload: ResponsesRequest) -> str | None:
    value = payload.prompt_cache_key
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    if not payload.model_extra:
        return None
    value = payload.model_extra.get("prompt_cache_key")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
