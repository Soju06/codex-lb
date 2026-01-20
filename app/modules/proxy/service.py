from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import AsyncIterator, Mapping
import anyio

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.clients.proxy import stream_responses as core_stream_responses
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error, response_failed_event
from app.core.openai.models import OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.usage.types import UsageWindowRow
from app.core.utils.request_id import ensure_request_id
from app.core.utils.sse import format_sse_event
from app.core.utils.time import utcnow
from app.db.models import Account, UsageHistory
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.helpers import (
    _apply_error_metadata,
    _credits_headers,
    _credits_snapshot,
    _header_account_id,
    _normalize_error_code,
    _parse_openai_error,
    _plan_type_for_accounts,
    _rate_limit_details,
    _rate_limit_headers,
    _select_accounts_for_limits,
    _summarize_window,
    _upstream_error_from_openai,
    _window_snapshot,
)
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.types import RateLimitStatusPayloadData
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)


class ProxyService:
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        usage_repo: UsageRepository,
        logs_repo: RequestLogsRepository,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._usage_repo = usage_repo
        self._logs_repo = logs_repo
        self._encryptor = TokenEncryptor()
        self._auth_manager = AuthManager(accounts_repo)
        self._load_balancer = LoadBalancer(accounts_repo, usage_repo)
        self._usage_updater = UsageUpdater(usage_repo, accounts_repo)

    def stream_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        propagate_http_errors: bool = False,
    ) -> AsyncIterator[str]:
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
        filtered = filter_inbound_headers(headers)
        selection = await self._load_balancer.select_account()
        account = selection.account
        if not account:
            raise ProxyResponseError(
                503,
                openai_error("no_accounts", selection.error_message or "No active accounts available"),
            )
        account = await self._ensure_fresh(account)
        account_id = _header_account_id(account.chatgpt_account_id)

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
        max_attempts = 3
        for attempt in range(max_attempts):
            selection = await self._load_balancer.select_account()
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
        account_id = _header_account_id(account.chatgpt_account_id)
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
                if event.type == "response.failed":
                    response = event.response
                    error = response.error if response else None
                else:
                    error = event.error
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
                        if event_type == "response.failed":
                            response = event.response
                            error = response.error if response else None
                        else:
                            error = event.error
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


class _RetryableStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error
