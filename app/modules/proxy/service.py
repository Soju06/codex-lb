from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import AsyncIterator, Mapping

import anyio

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.clients.proxy import stream_responses as core_stream_responses
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error, response_failed_event
from app.core.openai.models import OpenAIEvent, OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.core.usage.types import UsageWindowRow
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import Account, UsageHistory
from app.modules.accounts.auth_manager import AuthManager
from app.modules.api_keys.service import ApiKeyData, ApiKeysService, ApiKeyUsageReservationData
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
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.types import RateLimitStatusPayloadData
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)

_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_TEXT_DONE_CONTENT_PART_TYPES = frozenset({"output_text", "refusal"})


class ProxyService:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._encryptor = TokenEncryptor()
        self._load_balancer = LoadBalancer(repo_factory)

    def stream_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        propagate_http_errors: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
        suppress_text_done_events: bool = False,
    ) -> AsyncIterator[str]:
        _maybe_log_proxy_request_payload("stream", payload, headers)
        _maybe_log_proxy_request_shape("stream", payload, headers)
        filtered = filter_inbound_headers(headers)
        return self._stream_with_retry(
            payload,
            filtered,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
        )

    async def compact_responses(
        self,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
    ) -> OpenAIResponsePayload:
        _maybe_log_proxy_request_payload("compact", payload, headers)
        _maybe_log_proxy_request_shape("compact", payload, headers)
        filtered = filter_inbound_headers(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        response: OpenAIResponsePayload | None = None
        request_service_tier: str | None = None

        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        sticky_threads_enabled = settings.sticky_threads_enabled
        routing_strategy = getattr(settings, "routing_strategy", "usage_weighted")
        sticky_key, reallocate_sticky = _sticky_key_for_compact_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            sticky_threads_enabled=sticky_threads_enabled,
        )
        try:
            selection = await self._load_balancer.select_account(
                sticky_key=sticky_key,
                reallocate_sticky=reallocate_sticky,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=payload.model,
            )
            account = selection.account
            if not account:
                log_error_code = "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                raise ProxyResponseError(
                    503,
                    openai_error("no_accounts", log_error_message),
                )
            account_id_value = account.id
            account = await self._ensure_fresh(account)
            request_service_tier = _service_tier_from_compact_payload(payload)

            async def _call_compact(target: Account) -> OpenAIResponsePayload:
                access_token = self._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                return await core_compact_responses(payload, filtered, access_token, account_id)

            try:
                response = await _call_compact(account)
                await self._settle_compact_api_key_usage(
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    response=response,
                    request_service_tier=request_service_tier,
                )
                log_status = "success"
                return response
            except ProxyResponseError as exc:
                if exc.status_code != 401:
                    await self._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=None,
                        request_service_tier=request_service_tier,
                    )
                    await self._handle_proxy_error(account, exc)
                    raise
                try:
                    account = await self._ensure_fresh(account, force=True)
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    await self._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=None,
                        request_service_tier=request_service_tier,
                    )
                    raise exc
                try:
                    response = await _call_compact(account)
                    await self._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=response,
                        request_service_tier=request_service_tier,
                    )
                    log_status = "success"
                    return response
                except ProxyResponseError as exc:
                    await self._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=None,
                        request_service_tier=request_service_tier,
                    )
                    await self._handle_proxy_error(account, exc)
                    raise
        except ProxyResponseError as exc:
            error = _parse_openai_error(exc.payload)
            log_error_code = log_error_code or _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = log_error_message or (error.message if error else None)
            raise
        finally:
            usage = response.usage if response else None
            reasoning_effort = payload.reasoning.effort if payload.reasoning else None
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=payload.model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cached_input_tokens=(
                    usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
                ),
                reasoning_tokens=(
                    usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
                ),
                reasoning_effort=reasoning_effort,
                service_tier=request_service_tier if account_id_value else None,
            )

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str | None,
        prompt: str | None,
        headers: Mapping[str, str],
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:
        filtered = filter_inbound_headers(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        transcribe_model = "gpt-4o-transcribe"

        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        routing_strategy = getattr(settings, "routing_strategy", "usage_weighted")
        try:
            selection = await self._load_balancer.select_account(
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=None,
            )
            account = selection.account
            if not account:
                log_error_code = "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                raise ProxyResponseError(
                    503,
                    openai_error("no_accounts", log_error_message),
                )
            account_id_value = account.id

            async def _call_transcribe(target: Account) -> dict[str, JsonValue]:
                access_token = self._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                return await core_transcribe_audio(
                    audio_bytes,
                    filename=filename,
                    content_type=content_type,
                    prompt=prompt,
                    headers=filtered,
                    access_token=access_token,
                    account_id=account_id,
                )

            try:
                account = await self._ensure_fresh(account)
                result = await _call_transcribe(account)
                log_status = "success"
                return result
            except RefreshError as refresh_exc:
                if refresh_exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                raise ProxyResponseError(
                    401,
                    openai_error(
                        "invalid_api_key",
                        refresh_exc.message,
                        error_type="invalid_request_error",
                    ),
                ) from refresh_exc
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
                    result = await _call_transcribe(account)
                    log_status = "success"
                    return result
                except ProxyResponseError as exc:
                    await self._handle_proxy_error(account, exc)
                    raise
        except ProxyResponseError as exc:
            error = _parse_openai_error(exc.payload)
            log_error_code = log_error_code or _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = log_error_message or (error.message if error else None)
            raise
        finally:
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=transcribe_model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
            )

    async def _settle_compact_api_key_usage(
        self,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        response: OpenAIResponsePayload | None,
        request_service_tier: str | None,
    ) -> None:
        if api_key is None or api_key_reservation is None:
            return

        reservation_id = api_key_reservation.reservation_id
        usage = response.usage if response is not None else None
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        cached_input_tokens = usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else 0
        model_name = api_key_reservation.model or (getattr(response, "model", None) or "")
        response_service_tier = _service_tier_from_response(response)
        service_tier = (
            response_service_tier
            if isinstance(response_service_tier, str)
            else request_service_tier
            if isinstance(request_service_tier, str)
            else None
        )

        with anyio.CancelScope(shield=True):
            try:
                async with self._repo_factory() as repos:
                    api_keys_service = ApiKeysService(repos.api_keys)
                    if response is not None and input_tokens is not None and output_tokens is not None:
                        await api_keys_service.finalize_usage_reservation(
                            reservation_id,
                            model=model_name,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cached_input_tokens=cached_input_tokens or 0,
                            service_tier=service_tier,
                        )
                    else:
                        await api_keys_service.release_usage_reservation(reservation_id)
            except Exception:
                logger.warning(
                    "Failed to settle compact API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    get_request_id(),
                    exc_info=True,
                )

    async def _settle_stream_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        settlement: _StreamSettlement,
        request_id: str,
    ) -> bool:
        """Settle stream reservation. Returns True if settled."""
        if api_key is None or api_key_reservation is None:
            return True

        reservation_id = api_key_reservation.reservation_id
        model_name = api_key_reservation.model or settlement.model or ""

        settled: bool = False
        with anyio.CancelScope(shield=True):
            try:
                async with self._repo_factory() as repos:
                    api_keys_service = ApiKeysService(repos.api_keys)
                    if (
                        settlement.status == "success"
                        and settlement.input_tokens is not None
                        and settlement.output_tokens is not None
                    ):
                        await api_keys_service.finalize_usage_reservation(
                            reservation_id,
                            model=model_name,
                            input_tokens=settlement.input_tokens,
                            output_tokens=settlement.output_tokens,
                            cached_input_tokens=settlement.cached_input_tokens or 0,
                            service_tier=settlement.service_tier,
                        )
                    else:
                        await api_keys_service.release_usage_reservation(reservation_id)
                settled = True
            except Exception:
                logger.warning(
                    "Failed to settle stream API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    request_id,
                    exc_info=True,
                )
                settled = False

        return settled

    async def rate_limit_headers(self) -> dict[str, str]:
        return await get_rate_limit_headers_cache().get(self._compute_rate_limit_headers)

    async def _compute_rate_limit_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        async with self._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            selected_accounts = _select_accounts_for_limits(accounts)
            if not selected_accounts:
                return headers

            account_map = {account.id: account for account in selected_accounts}
            primary_rows_raw = await self._latest_usage_rows(repos, account_map, "primary")
            secondary_rows_raw = await self._latest_usage_rows(repos, account_map, "secondary")
            primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
                primary_rows_raw,
                secondary_rows_raw,
            )

            primary_summary = _summarize_window(primary_rows, account_map, "primary")
            if primary_summary is not None:
                headers.update(_rate_limit_headers("primary", primary_summary))

            secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")
            if secondary_summary is not None:
                headers.update(_rate_limit_headers("secondary", secondary_summary))

            headers.update(_credits_headers(await self._latest_usage_entries(repos, account_map)))
        return headers

    async def get_rate_limit_payload(self) -> RateLimitStatusPayloadData:
        async with self._repo_factory() as repos:
            accounts = await repos.accounts.list_accounts()
            await self._refresh_usage(repos, accounts)
            selected_accounts = _select_accounts_for_limits(accounts)
            if not selected_accounts:
                return RateLimitStatusPayloadData(plan_type="guest")

            account_map = {account.id: account for account in selected_accounts}
            primary_rows_raw = await self._latest_usage_rows(repos, account_map, "primary")
            secondary_rows_raw = await self._latest_usage_rows(repos, account_map, "secondary")
            primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
                primary_rows_raw,
                secondary_rows_raw,
            )

            primary_summary = _summarize_window(primary_rows, account_map, "primary")
            secondary_summary = _summarize_window(secondary_rows, account_map, "secondary")

            now_epoch = int(time.time())
            primary_window = _window_snapshot(primary_summary, primary_rows, "primary", now_epoch)
            secondary_window = _window_snapshot(secondary_summary, secondary_rows, "secondary", now_epoch)

            return RateLimitStatusPayloadData(
                plan_type=_plan_type_for_accounts(selected_accounts),
                rate_limit=_rate_limit_details(primary_window, secondary_window),
                credits=_credits_snapshot(await self._latest_usage_entries(repos, account_map)),
            )

    async def _stream_with_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        propagate_http_errors: bool,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool,
    ) -> AsyncIterator[str]:
        request_id = ensure_request_id()
        start = time.monotonic()
        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        sticky_threads_enabled = settings.sticky_threads_enabled
        routing_strategy = getattr(settings, "routing_strategy", "usage_weighted")
        sticky_key = _sticky_key_for_responses_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            sticky_threads_enabled=sticky_threads_enabled,
        )
        max_attempts = 3
        settled = False
        settlement = _StreamSettlement()
        try:
            for attempt in range(max_attempts):
                selection = await self._load_balancer.select_account(
                    sticky_key=sticky_key,
                    prefer_earlier_reset_accounts=prefer_earlier_reset,
                    routing_strategy=routing_strategy,
                    model=payload.model,
                )
                account = selection.account
                if not account:
                    no_accounts_msg = selection.error_message or "No active accounts available"
                    event = response_failed_event(
                        "no_accounts",
                        no_accounts_msg,
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    await self._write_request_log(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="error",
                        error_code="no_accounts",
                        error_message=no_accounts_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        service_tier=payload.service_tier,
                    )
                    return

                account_id_value = account.id
                try:
                    account = await self._ensure_fresh(account)
                    settlement = _StreamSettlement()
                    async for line in self._stream_once(
                        account,
                        payload,
                        headers,
                        request_id,
                        attempt < max_attempts - 1,
                        api_key=api_key,
                        settlement=settlement,
                        suppress_text_done_events=suppress_text_done_events,
                    ):
                        yield line
                    settled = await self._settle_stream_api_key_usage(
                        api_key,
                        api_key_reservation,
                        settlement,
                        request_id,
                    )
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
                        settlement = _StreamSettlement()
                        async for line in self._stream_once(
                            account,
                            payload,
                            headers,
                            request_id,
                            False,
                            api_key=api_key,
                            settlement=settlement,
                            suppress_text_done_events=suppress_text_done_events,
                        ):
                            yield line
                        settled = await self._settle_stream_api_key_usage(
                            api_key,
                            api_key_reservation,
                            settlement,
                            request_id,
                        )
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
        finally:
            if not settled and api_key is not None and api_key_reservation is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        async with self._repo_factory() as repos:
                            api_keys_service = ApiKeysService(repos.api_keys)
                            await api_keys_service.release_usage_reservation(
                                api_key_reservation.reservation_id,
                            )
                    except Exception:
                        logger.warning(
                            "Failed to release stream API key reservation key_id=%s request_id=%s",
                            api_key.id,
                            request_id,
                            exc_info=True,
                        )

    async def _stream_once(
        self,
        account: Account,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        request_id: str,
        allow_retry: bool,
        *,
        api_key: ApiKeyData | None,
        settlement: _StreamSettlement,
        suppress_text_done_events: bool,
    ) -> AsyncIterator[str]:
        account_id_value = account.id
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        account_id = _header_account_id(account.chatgpt_account_id)
        model = payload.model
        service_tier = payload.service_tier
        reasoning_effort = payload.reasoning.effort if payload.reasoning else None
        start = time.monotonic()
        status = "success"
        error_code = None
        error_message = None
        usage = None
        saw_text_delta = False

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
            first_payload = parse_sse_data_json(first)
            event = parse_sse_event(first)
            event_type = _event_type_from_payload(event, first_payload)
            actual_service_tier = _service_tier_from_event_payload(first_payload)
            if actual_service_tier is not None:
                service_tier = actual_service_tier
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

            if event and event.type in ("response.completed", "response.incomplete"):
                usage = event.response.usage if event.response else None
                if event.type == "response.incomplete":
                    status = "error"

            if suppress_text_done_events and event_type in _TEXT_DELTA_EVENT_TYPES:
                saw_text_delta = True
            if not _should_suppress_text_done_event(
                event_type=event_type,
                payload=first_payload,
                suppress_text_done_events=suppress_text_done_events,
                saw_text_delta=saw_text_delta,
            ):
                yield first

            async for line in iterator:
                event_payload = parse_sse_data_json(line)
                event = parse_sse_event(line)
                event_type = _event_type_from_payload(event, event_payload)
                actual_service_tier = _service_tier_from_event_payload(event_payload)
                if actual_service_tier is not None:
                    service_tier = actual_service_tier
                if suppress_text_done_events and event_type in _TEXT_DELTA_EVENT_TYPES:
                    saw_text_delta = True
                if _should_suppress_text_done_event(
                    event_type=event_type,
                    payload=event_payload,
                    suppress_text_done_events=suppress_text_done_events,
                    saw_text_delta=saw_text_delta,
                ):
                    continue
                if event:
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
                    if event_type in ("response.completed", "response.incomplete"):
                        usage = event.response.usage if event.response else None
                        if event_type == "response.incomplete":
                            status = "error"
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
            input_tokens = usage.input_tokens if usage else None
            output_tokens = usage.output_tokens if usage else None
            cached_input_tokens = (
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            )
            reasoning_tokens = (
                usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
            )
            settlement.status = status
            settlement.model = model
            settlement.service_tier = service_tier
            settlement.input_tokens = input_tokens
            settlement.output_tokens = output_tokens
            settlement.cached_input_tokens = cached_input_tokens
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=model,
                latency_ms=int((time.monotonic() - start) * 1000),
                status=status,
                error_code=error_code,
                error_message=error_message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=reasoning_effort,
                service_tier=service_tier,
            )

    async def _write_request_log(
        self,
        *,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_id: str,
        model: str | None,
        latency_ms: int,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
    ) -> None:
        with anyio.CancelScope(shield=True):
            try:
                async with self._repo_factory() as repos:
                    await repos.request_logs.add_log(
                        account_id=account_id,
                        api_key_id=api_key.id if api_key else None,
                        request_id=request_id,
                        model=model or "",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens,
                        reasoning_tokens=reasoning_tokens,
                        reasoning_effort=reasoning_effort,
                        service_tier=service_tier,
                        latency_ms=latency_ms,
                        status=status,
                        error_code=error_code,
                        error_message=error_message,
                    )
            except Exception:
                logger.warning(
                    "Failed to persist request log account_id=%s request_id=%s",
                    account_id,
                    request_id,
                    exc_info=True,
                )

    async def _refresh_usage(self, repos: ProxyRepositories, accounts: list[Account]) -> None:
        latest_usage = await repos.usage.latest_by_account(window="primary")
        updater = UsageUpdater(repos.usage, repos.accounts)
        await updater.refresh_accounts(accounts, latest_usage)

    async def _latest_usage_rows(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
        window: str,
    ) -> list[UsageWindowRow]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account(window=window)
        return [
            UsageWindowRow(
                account_id=entry.account_id,
                used_percent=entry.used_percent,
                reset_at=entry.reset_at,
                window_minutes=entry.window_minutes,
                recorded_at=entry.recorded_at,
            )
            for entry in latest.values()
            if entry.account_id in account_map
        ]

    async def _latest_usage_entries(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
    ) -> list[UsageHistory]:
        if not account_map:
            return []
        latest = await repos.usage.latest_by_account()
        return [entry for entry in latest.values() if entry.account_id in account_map]

    async def _ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        async with self._repo_factory() as repos:
            auth_manager = AuthManager(repos.accounts)
            return await auth_manager.ensure_fresh(account, force=force)

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


@dataclass
class _StreamSettlement:
    """Populated by _stream_once(), consumed by _stream_with_retry() for reservation settlement."""

    status: str = "success"
    model: str = ""
    service_tier: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None


def _event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    return None


def _should_suppress_text_done_event(
    *,
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    suppress_text_done_events: bool,
    saw_text_delta: bool,
) -> bool:
    if not suppress_text_done_events or not saw_text_delta or event_type is None:
        return False
    if event_type == "response.output_text.done":
        return True
    if event_type == "response.content_part.done":
        return _is_text_content_part(payload)
    return False


def _is_text_content_part(payload: dict[str, JsonValue] | None) -> bool:
    if payload is None:
        return False
    part = payload.get("part")
    if not isinstance(part, dict):
        return False
    part_type = part.get("type")
    return isinstance(part_type, str) and part_type in _TEXT_DONE_CONTENT_PART_TYPES


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
        "proxy_request_shape request_id=%s kind=%s model=%s stream=%s input=%s "
        "prompt_cache_key=%s prompt_cache_key_raw=%s fields=%s extra=%s headers=%s",
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


def _maybe_log_proxy_request_payload(
    kind: str,
    payload: ResponsesRequest | ResponsesCompactRequest,
    headers: Mapping[str, str],
) -> None:
    settings = get_settings()
    if not settings.log_proxy_request_payload:
        return

    request_id = get_request_id()
    payload_dict = payload.model_dump(mode="json", exclude_none=True)
    extra = payload.model_extra or {}
    if extra:
        payload_dict = {**payload_dict, "_extra": extra}
    header_keys = _interesting_header_keys(headers)
    payload_json = json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))

    logger.warning(
        "proxy_request_payload request_id=%s kind=%s payload=%s headers=%s",
        request_id,
        kind,
        payload_json,
        header_keys,
    )


def _hash_identifier(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _summarize_input(items: JsonValue) -> str:
    if items is None:
        return "0"
    if isinstance(items, str):
        return "str"
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        if not items:
            return "0"
        type_counts: dict[str, int] = {}
        for item in items:
            type_name = type(item).__name__
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
        summary = ",".join(f"{key}={type_counts[key]}" for key in sorted(type_counts))
        return f"{len(items)}({summary})"
    return type(items).__name__


def _truncate_identifier(value: str, *, max_length: int = 96) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:48]}...{value[-16:]}"


def _interesting_header_keys(headers: Mapping[str, str]) -> list[str]:
    allowlist = {
        "user-agent",
        "x-request-id",
        "request-id",
        "session_id",
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


def _sticky_key_from_session_header(headers: Mapping[str, str]) -> str | None:
    for key, value in headers.items():
        if key.lower() != "session_id":
            continue
        stripped = value.strip()
        return stripped or None
    return None


def _sticky_key_for_responses_request(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    sticky_threads_enabled: bool,
) -> str | None:
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return session_key
    if sticky_threads_enabled:
        return _sticky_key_from_payload(payload)
    return None


def _sticky_key_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    if not payload.model_extra:
        return None
    value = payload.model_extra.get("prompt_cache_key")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_for_compact_request(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    sticky_threads_enabled: bool,
) -> tuple[str | None, bool]:
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return session_key, False
    if sticky_threads_enabled:
        return _sticky_key_from_compact_payload(payload), True
    return None, False


def _service_tier_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    if not payload.model_extra:
        return None
    return _normalize_service_tier_value(payload.model_extra.get("service_tier"))


def _service_tier_from_response(response: OpenAIResponsePayload | None) -> str | None:
    if response is None:
        return None
    extra = response.model_extra
    if not isinstance(extra, Mapping):
        return None
    return _normalize_service_tier_value(extra.get("service_tier"))


def _service_tier_from_event_payload(payload: dict[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    return _normalize_service_tier_value(response.get("service_tier"))


def _normalize_service_tier_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
