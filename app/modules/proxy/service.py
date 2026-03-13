from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import AsyncIterator, Mapping, NoReturn
from uuid import uuid4

import aiohttp
import anyio
from fastapi import WebSocket
from pydantic import ValidationError

from app.core import usage as usage_core
from app.core.auth.refresh import (
    RefreshError,
    pop_token_refresh_timeout_override,
    push_token_refresh_timeout_override,
)
from app.core.balancer import PERMANENT_FAILURE_CODES, RoutingStrategy
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import (
    ProxyResponseError,
    filter_inbound_headers,
    pop_compact_timeout_overrides,
    pop_stream_timeout_overrides,
    pop_transcribe_timeout_overrides,
    push_compact_timeout_overrides,
    push_stream_timeout_overrides,
    push_transcribe_timeout_overrides,
)
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.clients.proxy import stream_responses as core_stream_responses
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio
from app.core.clients.proxy_websocket import (
    UpstreamResponsesWebSocket,
    UpstreamWebSocketMessage,
    connect_responses_websocket,
    filter_inbound_websocket_headers,
)
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.errors import OpenAIErrorEnvelope, ResponseFailedEvent, openai_error, response_failed_event
from app.core.exceptions import AppError, ProxyAuthError, ProxyRateLimitError
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.models import CompactResponsePayload, OpenAIEvent, OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.core.usage.types import UsageWindowRow
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import Account, DashboardSettings, StickySessionKind, UsageHistory
from app.modules.accounts.auth_manager import AuthManager
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
    ApiKeyUsageReservationData,
)
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
from app.modules.proxy.load_balancer import AccountSelection, LoadBalancer
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    normalize_responses_request_payload,
    openai_invalid_payload_error,
    openai_validation_error,
    validate_model_access,
)
from app.modules.proxy.types import (
    AdditionalRateLimitData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)
from app.modules.usage.additional_quota_keys import get_additional_display_label_for_quota_key
from app.modules.usage.updater import UsageUpdater

logger = logging.getLogger(__name__)

_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_TEXT_DONE_CONTENT_PART_TYPES = frozenset({"output_text", "refusal"})
_REQUEST_TRANSPORT_HTTP = "http"
_REQUEST_TRANSPORT_WEBSOCKET = "websocket"
_COMPACT_SAME_CONTRACT_RETRY_BUDGET = 1
_ACCOUNT_RECOVERY_RETRY_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "usage_limit_reached",
        "insufficient_quota",
        "usage_not_included",
        "quota_exceeded",
        *PERMANENT_FAILURE_CODES.keys(),
    }
)


@dataclass(frozen=True, slots=True)
class _AffinityPolicy:
    key: str | None = None
    kind: StickySessionKind | None = None
    reallocate_sticky: bool = False
    max_age_seconds: int | None = None


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
        openai_cache_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
        suppress_text_done_events: bool = False,
        request_transport: str = _REQUEST_TRANSPORT_HTTP,
    ) -> AsyncIterator[str]:
        _maybe_log_proxy_request_payload("stream", payload, headers)
        _maybe_log_proxy_request_shape("stream", payload, headers)
        filtered = filter_inbound_headers(headers)
        return self._stream_with_retry(
            payload,
            filtered,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=propagate_http_errors,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
            request_transport=request_transport,
        )

    async def compact_responses(
        self,
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool = False,
        openai_cache_affinity: bool = False,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
    ) -> CompactResponsePayload:
        _maybe_log_proxy_request_payload("compact", payload, headers)
        _maybe_log_proxy_request_shape("compact", payload, headers)
        filtered = filter_inbound_headers(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        base_settings = get_settings()
        deadline = start + base_settings.compact_request_budget_seconds
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        response: CompactResponsePayload | None = None
        request_service_tier: str | None = None
        actual_service_tier: str | None = None

        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        affinity = _sticky_key_for_compact_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=settings.sticky_threads_enabled,
        )
        routing_strategy = _routing_strategy(settings)
        try:
            selection = await self._select_account_with_budget(
                deadline,
                request_id=request_id,
                kind="compact",
                sticky_key=affinity.key,
                sticky_kind=affinity.kind,
                reallocate_sticky=affinity.reallocate_sticky,
                sticky_max_age_seconds=affinity.max_age_seconds,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=payload.model,
            )
            account = selection.account
            if not account:
                log_error_code = selection.error_code or "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                raise ProxyResponseError(
                    503,
                    openai_error(log_error_code, log_error_message),
                )
            account_id_value = account.id
            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                logger.warning("Compact request budget exhausted before freshness check request_id=%s", request_id)
                _raise_proxy_budget_exhausted()
            try:
                account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "Compact refresh/connect failed request_id=%s account_id=%s",
                    request_id,
                    account.id,
                    exc_info=True,
                )
                _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")
            request_service_tier = _service_tier_from_compact_payload(payload)

            async def _call_compact(target: Account) -> CompactResponsePayload:
                access_token = self._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Compact request budget exhausted before upstream call request_id=%s account_id=%s",
                        request_id,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()
                if base_settings.upstream_compact_timeout_seconds is None:
                    timeout_tokens = push_compact_timeout_overrides(
                        connect_timeout_seconds=remaining_budget,
                    )
                else:
                    timeout_tokens = push_compact_timeout_overrides(
                        connect_timeout_seconds=remaining_budget,
                        total_timeout_seconds=remaining_budget,
                    )
                try:
                    return await core_compact_responses(payload, filtered, access_token, account_id)
                finally:
                    pop_compact_timeout_overrides(timeout_tokens)

            safe_retry_budget = _COMPACT_SAME_CONTRACT_RETRY_BUDGET
            refresh_retry_used = False
            while True:
                try:
                    response = await _call_compact(account)
                    actual_service_tier = _service_tier_from_response(response)
                    await self._load_balancer.record_success(account)
                    await self._settle_compact_api_key_usage(
                        api_key=api_key,
                        api_key_reservation=api_key_reservation,
                        response=response,
                        request_service_tier=request_service_tier,
                    )
                    log_status = "success"
                    return response
                except ProxyResponseError as exc:
                    if exc.status_code == 401:
                        if refresh_retry_used:
                            await self._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            await self._handle_proxy_error(account, exc)
                            raise
                        try:
                            remaining_budget = _remaining_budget_seconds(deadline)
                            if remaining_budget <= 0:
                                logger.warning(
                                    "Compact request budget exhausted before forced refresh retry request_id=%s "
                                    "account_id=%s",
                                    request_id,
                                    account.id,
                                )
                                _raise_proxy_budget_exhausted()
                            account = await self._ensure_fresh_with_budget(
                                account,
                                force=True,
                                timeout_seconds=remaining_budget,
                            )
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
                        except (aiohttp.ClientError, asyncio.TimeoutError) as timeout_exc:
                            await self._settle_compact_api_key_usage(
                                api_key=api_key,
                                api_key_reservation=api_key_reservation,
                                response=None,
                                request_service_tier=request_service_tier,
                            )
                            logger.warning(
                                "Compact forced refresh/connect failed request_id=%s account_id=%s",
                                request_id,
                                account.id,
                                exc_info=True,
                            )
                            _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                        refresh_retry_used = True
                        continue
                    if exc.retryable_same_contract and safe_retry_budget > 0:
                        safe_retry_budget -= 1
                        continue
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
                transport=_REQUEST_TRANSPORT_HTTP,
                service_tier=_service_tier_from_response(response) or _service_tier_from_compact_payload(payload),
            )
            _maybe_log_proxy_service_tier_trace(
                "compact",
                requested_service_tier=request_service_tier,
                actual_service_tier=actual_service_tier,
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
        base_settings = get_settings()
        deadline = start + base_settings.transcription_request_budget_seconds
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        transcribe_model = "gpt-4o-transcribe"

        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        routing_strategy = _routing_strategy(settings)
        try:
            selection = await self._select_account_with_budget(
                deadline,
                request_id=request_id,
                kind="transcribe",
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=None,
            )
            account = selection.account
            if not account:
                log_error_code = selection.error_code or "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                raise ProxyResponseError(
                    503,
                    openai_error(log_error_code, log_error_message),
                )
            account_id_value = account.id

            async def _call_transcribe(target: Account) -> dict[str, JsonValue]:
                access_token = self._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Transcription request budget exhausted before upstream call request_id=%s account_id=%s",
                        request_id,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()
                timeout_tokens = push_transcribe_timeout_overrides(
                    connect_timeout_seconds=remaining_budget,
                    total_timeout_seconds=remaining_budget,
                )
                try:
                    return await core_transcribe_audio(
                        audio_bytes,
                        filename=filename,
                        content_type=content_type,
                        prompt=prompt,
                        headers=filtered,
                        access_token=access_token,
                        account_id=account_id,
                    )
                finally:
                    pop_transcribe_timeout_overrides(timeout_tokens)

            try:
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Transcription request budget exhausted before freshness check request_id=%s", request_id
                    )
                    _raise_proxy_budget_exhausted()
                try:
                    account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    logger.warning(
                        "Transcription refresh/connect failed request_id=%s account_id=%s",
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")
                result = await _call_transcribe(account)
                await self._load_balancer.record_success(account)
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
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "Transcription request budget exhausted before forced refresh retry "
                            "request_id=%s account_id=%s",
                            request_id,
                            account.id,
                        )
                        _raise_proxy_budget_exhausted()
                    account = await self._ensure_fresh_with_budget(
                        account, force=True, timeout_seconds=remaining_budget
                    )
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    raise exc
                except (aiohttp.ClientError, asyncio.TimeoutError) as timeout_exc:
                    logger.warning(
                        "Transcription forced refresh/connect failed request_id=%s account_id=%s",
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                try:
                    result = await _call_transcribe(account)
                    await self._load_balancer.record_success(account)
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
                transport=_REQUEST_TRANSPORT_HTTP,
            )

    async def proxy_responses_websocket(
        self,
        websocket: WebSocket,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
    ) -> None:
        filtered_headers = filter_inbound_websocket_headers(dict(headers))
        runtime_settings = get_settings()
        settings = await get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        sticky_threads_enabled = bool(getattr(settings, "sticky_threads_enabled", False))
        openai_cache_affinity_max_age_seconds = int(getattr(settings, "openai_cache_affinity_max_age_seconds", 300))
        routing_strategy = _routing_strategy(settings)
        pending_requests: deque[_WebSocketRequestState] = deque()
        pending_lock = anyio.Lock()
        client_send_lock = anyio.Lock()
        response_create_gate = asyncio.Semaphore(1)
        upstream: UpstreamResponsesWebSocket | None = None
        upstream_reader: asyncio.Task[None] | None = None
        upstream_control: _WebSocketUpstreamControl | None = None
        account: Account | None = None

        try:
            while True:
                message = await websocket.receive()
                message_type = message["type"]

                if message_type == "websocket.disconnect":
                    break
                if message_type != "websocket.receive":
                    continue

                if upstream_reader is not None and upstream_reader.done():
                    try:
                        await upstream_reader
                    except asyncio.CancelledError:
                        pass
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    account = None

                text_data = message.get("text")
                bytes_data = message.get("bytes")
                request_state: _WebSocketRequestState | None = None
                request_state_registered = False
                request_affinity = _AffinityPolicy()
                payload: dict[str, JsonValue] | None = None

                if text_data is not None:
                    payload = _parse_websocket_payload(text_data)
                    if payload is not None and _is_websocket_response_create(payload):
                        try:
                            prepared_request = await self._prepare_websocket_response_create_request(
                                payload,
                                headers=headers,
                                codex_session_affinity=codex_session_affinity,
                                openai_cache_affinity=openai_cache_affinity,
                                sticky_threads_enabled=sticky_threads_enabled,
                                openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
                                api_key=api_key,
                            )
                            request_state = prepared_request.request_state
                            request_affinity = prepared_request.affinity_policy
                            text_data = prepared_request.text_data
                        except AppError as exc:
                            async with client_send_lock:
                                await websocket.send_text(
                                    _serialize_websocket_error_event(_app_error_to_websocket_event(exc))
                                )
                            continue
                        except ClientPayloadError as exc:
                            async with client_send_lock:
                                await websocket.send_text(
                                    _serialize_websocket_error_event(
                                        _wrapped_websocket_error_event(400, openai_invalid_payload_error(exc.param))
                                    )
                                )
                            continue
                        except ValidationError as exc:
                            async with client_send_lock:
                                await websocket.send_text(
                                    _serialize_websocket_error_event(
                                        _wrapped_websocket_error_event(400, openai_validation_error(exc))
                                    )
                                )
                            continue

                if (
                    request_state is not None
                    and upstream_control is not None
                    and upstream_control.reconnect_requested
                    and upstream_reader is not None
                ):
                    await upstream_reader
                    upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket", exc_info=True)
                    upstream = None
                    account = None

                if request_state is not None and upstream is not None:
                    async with pending_lock:
                        has_pending_requests = bool(pending_requests)
                    if not has_pending_requests:
                        if upstream_reader is not None:
                            upstream_reader.cancel()
                            try:
                                await upstream_reader
                            except asyncio.CancelledError:
                                pass
                            upstream_reader = None
                        upstream_control = None
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close idle upstream websocket before reselect", exc_info=True)
                        upstream = None
                        account = None

                if request_state is not None:
                    await response_create_gate.acquire()
                    async with pending_lock:
                        pending_requests.append(request_state)
                    request_state_registered = True

                if upstream is None:
                    if text_data is not None and payload is None:
                        async with client_send_lock:
                            await websocket.send_text(
                                _serialize_websocket_error_event(
                                    _wrapped_websocket_error_event(400, openai_invalid_payload_error())
                                )
                            )
                        continue
                    if request_state is None:
                        async with client_send_lock:
                            await websocket.send_text(
                                _serialize_websocket_error_event(
                                    _wrapped_websocket_error_event(
                                        400,
                                        openai_error(
                                            "invalid_request_error",
                                            "WebSocket connection has no active upstream session",
                                            error_type="invalid_request_error",
                                        ),
                                    )
                                )
                            )
                        continue
                    account, upstream = await self._connect_proxy_websocket(
                        filtered_headers,
                        sticky_key=request_affinity.key,
                        sticky_kind=request_affinity.kind,
                        reallocate_sticky=request_affinity.reallocate_sticky,
                        sticky_max_age_seconds=request_affinity.max_age_seconds,
                        prefer_earlier_reset=prefer_earlier_reset,
                        routing_strategy=routing_strategy,
                        model=request_state.model,
                        request_state=request_state,
                        api_key=api_key,
                        client_send_lock=client_send_lock,
                        websocket=websocket,
                    )
                    if upstream is None or account is None:
                        if request_state_registered:
                            async with pending_lock:
                                if request_state in pending_requests:
                                    pending_requests.remove(request_state)
                            _release_websocket_response_create_gate(request_state, response_create_gate)
                        continue
                    upstream_control = _WebSocketUpstreamControl()
                    upstream_reader = asyncio.create_task(
                        self._relay_upstream_websocket_messages(
                            websocket,
                            upstream,
                            account=account,
                            account_id_value=account.id,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            client_send_lock=client_send_lock,
                            api_key=api_key,
                            upstream_control=upstream_control,
                            response_create_gate=response_create_gate,
                            proxy_request_budget_seconds=runtime_settings.proxy_request_budget_seconds,
                            stream_idle_timeout_seconds=runtime_settings.stream_idle_timeout_seconds,
                        )
                    )

                try:
                    if text_data is not None:
                        await upstream.send_text(text_data)
                    elif bytes_data is not None:
                        await upstream.send_bytes(bytes_data)
                except Exception:
                    await self._fail_pending_websocket_requests(
                        account_id_value=account.id if account else None,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        error_code="stream_incomplete",
                        error_message="Upstream websocket closed before response.completed",
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                    )
                    if upstream_reader is not None:
                        upstream_reader.cancel()
                        try:
                            await upstream_reader
                        except asyncio.CancelledError:
                            pass
                        upstream_reader = None
                    upstream_control = None
                    if upstream is not None:
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket after send failure", exc_info=True)
                    upstream = None
                    account = None
                    continue
        finally:
            if upstream_reader is not None:
                upstream_reader.cancel()
                try:
                    await upstream_reader
                except asyncio.CancelledError:
                    pass
            if upstream is not None:
                try:
                    await upstream.close()
                except Exception:
                    logger.debug("Failed to close upstream websocket", exc_info=True)
            await self._fail_pending_websocket_requests(
                account_id_value=account.id if account else None,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="Upstream websocket closed before response.completed",
                api_key=api_key,
                websocket=websocket,
                client_send_lock=client_send_lock,
                response_create_gate=response_create_gate,
            )

    async def _prepare_websocket_response_create_request(
        self,
        payload: dict[str, JsonValue],
        *,
        headers: Mapping[str, str],
        codex_session_affinity: bool,
        openai_cache_affinity: bool,
        sticky_threads_enabled: bool,
        openai_cache_affinity_max_age_seconds: int,
        api_key: ApiKeyData | None,
    ) -> _PreparedWebSocketRequest:
        refreshed_api_key = await self._refresh_websocket_api_key_policy(api_key)
        responses_payload = normalize_responses_request_payload(payload, openai_compat=openai_cache_affinity)
        apply_api_key_enforcement(responses_payload, refreshed_api_key)
        validate_model_access(refreshed_api_key, responses_payload.model)

        upstream_payload = dict(responses_payload.to_payload())
        forwarded_service_tier = _normalize_service_tier_value(upstream_payload.get("service_tier"))
        reservation = await self._reserve_websocket_api_key_usage(
            refreshed_api_key,
            request_model=responses_payload.model,
            request_service_tier=forwarded_service_tier,
        )

        return _PreparedWebSocketRequest(
            text_data=json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":")),
            request_state=_WebSocketRequestState(
                request_id=f"ws_{uuid4().hex}",
                model=responses_payload.model,
                service_tier=forwarded_service_tier,
                reasoning_effort=responses_payload.reasoning.effort if responses_payload.reasoning else None,
                api_key_reservation=reservation,
                started_at=time.monotonic(),
                awaiting_response_created=True,
            ),
            affinity_policy=_sticky_key_for_responses_request(
                responses_payload,
                headers,
                codex_session_affinity=codex_session_affinity,
                openai_cache_affinity=openai_cache_affinity,
                openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
                sticky_threads_enabled=sticky_threads_enabled,
            ),
        )

    async def _connect_proxy_websocket(
        self,
        headers: dict[str, str],
        *,
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        prefer_earlier_reset: bool,
        routing_strategy: RoutingStrategy,
        model: str | None,
        request_state: _WebSocketRequestState,
        api_key: ApiKeyData | None,
        client_send_lock: anyio.Lock,
        websocket: WebSocket,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
    ) -> tuple[Account | None, UpstreamResponsesWebSocket | None]:
        deadline = _websocket_connect_deadline(request_state, get_settings().proxy_request_budget_seconds)
        try:
            selection = await self._select_account_with_budget(
                deadline,
                request_id=request_state.request_id,
                kind="websocket",
                sticky_key=sticky_key,
                sticky_kind=sticky_kind,
                reallocate_sticky=reallocate_sticky,
                sticky_max_age_seconds=sticky_max_age_seconds,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=model,
            )
        except ProxyResponseError as exc:
            if _is_proxy_budget_exhausted_error(exc):
                await self._emit_websocket_proxy_request_timeout(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=None,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None, None
            raise

        account = selection.account
        if not account:
            error_code = selection.error_code or "no_accounts"
            error_message = selection.error_message or "No active accounts available"
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=None,
                api_key=api_key,
                request_state=request_state,
                status_code=503,
                payload=openai_error(
                    error_code,
                    error_message,
                    error_type="server_error",
                ),
                error_code=error_code,
                error_message=error_message,
            )
            return None, None

        try:
            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await self._emit_websocket_proxy_request_timeout(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None, None
            account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)

            remaining_budget = _remaining_budget_seconds(deadline)
            if remaining_budget <= 0:
                await self._emit_websocket_proxy_request_timeout(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None, None
            return account, await self._open_upstream_websocket_with_budget(
                account,
                headers,
                timeout_seconds=remaining_budget,
            )
        except ProxyResponseError as exc:
            if _is_proxy_budget_exhausted_error(exc):
                await self._emit_websocket_proxy_request_timeout(
                    websocket,
                    client_send_lock=client_send_lock,
                    account_id=account.id,
                    api_key=api_key,
                    request_state=request_state,
                )
                return None, None
            if exc.status_code == 401:
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        await self._emit_websocket_proxy_request_timeout(
                            websocket,
                            client_send_lock=client_send_lock,
                            account_id=account.id,
                            api_key=api_key,
                            request_state=request_state,
                        )
                        return None, None
                    account = await self._ensure_fresh_with_budget(
                        account,
                        force=True,
                        timeout_seconds=remaining_budget,
                    )
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    await self._emit_websocket_connect_failure(
                        websocket,
                        client_send_lock=client_send_lock,
                        account_id=account.id,
                        api_key=api_key,
                        request_state=request_state,
                        status_code=401,
                        payload=openai_error(
                            "invalid_api_key",
                            refresh_exc.message,
                            error_type="authentication_error",
                        ),
                        error_code="invalid_api_key",
                        error_message=refresh_exc.message,
                    )
                    return None, None
                except (aiohttp.ClientError, asyncio.TimeoutError) as refresh_transport_exc:
                    message = str(refresh_transport_exc) or "Request to upstream timed out"
                    await self._emit_websocket_connect_failure(
                        websocket,
                        client_send_lock=client_send_lock,
                        account_id=account.id,
                        api_key=api_key,
                        request_state=request_state,
                        status_code=502,
                        payload=openai_error(
                            "upstream_unavailable",
                            message,
                            error_type="server_error",
                        ),
                        error_code="upstream_unavailable",
                        error_message=message,
                    )
                    return None, None
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        await self._emit_websocket_proxy_request_timeout(
                            websocket,
                            client_send_lock=client_send_lock,
                            account_id=account.id,
                            api_key=api_key,
                            request_state=request_state,
                        )
                        return None, None
                    return account, await self._open_upstream_websocket_with_budget(
                        account,
                        headers,
                        timeout_seconds=remaining_budget,
                    )
                except ProxyResponseError as retry_exc:
                    exc = retry_exc
                    if _is_proxy_budget_exhausted_error(exc):
                        await self._emit_websocket_proxy_request_timeout(
                            websocket,
                            client_send_lock=client_send_lock,
                            account_id=account.id,
                            api_key=api_key,
                            request_state=request_state,
                        )
                        return None, None
            await self._handle_websocket_connect_error(account, exc)
            error = _parse_openai_error(exc.payload)
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=exc.status_code,
                payload=exc.payload,
                error_code=error_code or "upstream_error",
                error_message=error_message or "Upstream error",
            )
            return None, None
        except RefreshError as exc:
            if exc.is_permanent:
                await self._load_balancer.mark_permanent_failure(account, exc.code)
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=401,
                payload=openai_error(
                    "invalid_api_key",
                    exc.message,
                    error_type="authentication_error",
                ),
                error_code="invalid_api_key",
                error_message=exc.message,
            )
            return None, None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            message = str(exc) or "Request to upstream timed out"
            await self._emit_websocket_connect_failure(
                websocket,
                client_send_lock=client_send_lock,
                account_id=account.id,
                api_key=api_key,
                request_state=request_state,
                status_code=502,
                payload=openai_error(
                    "upstream_unavailable",
                    message,
                    error_type="server_error",
                ),
                error_code="upstream_unavailable",
                error_message=message,
            )
            return None, None

    async def _open_upstream_websocket_with_budget(
        self,
        account: Account,
        headers: dict[str, str],
        *,
        timeout_seconds: float,
    ) -> UpstreamResponsesWebSocket:
        try:
            with anyio.fail_after(timeout_seconds):
                return await self._open_upstream_websocket(account, headers)
        except TimeoutError:
            _raise_proxy_budget_exhausted()

    async def _open_upstream_websocket(
        self,
        account: Account,
        headers: dict[str, str],
    ) -> UpstreamResponsesWebSocket:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        account_id = _header_account_id(account.chatgpt_account_id)
        return await connect_responses_websocket(headers, access_token, account_id)

    async def _refresh_websocket_api_key_policy(self, api_key: ApiKeyData | None) -> ApiKeyData | None:
        if api_key is None:
            return None

        with anyio.CancelScope(shield=True):
            async with self._repo_factory() as repos:
                service = ApiKeysService(repos.api_keys)
                try:
                    return await service.get_key_by_id(api_key.id)
                except ApiKeyInvalidError as exc:
                    raise ProxyAuthError(str(exc)) from exc

    async def _handle_websocket_connect_error(self, account: Account, exc: ProxyResponseError) -> None:
        error = _parse_openai_error(exc.payload)
        error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
        await self._handle_stream_error(
            account,
            _upstream_error_from_openai(error),
            error_code,
        )

    async def _relay_upstream_websocket_messages(
        self,
        websocket: WebSocket,
        upstream: UpstreamResponsesWebSocket,
        *,
        account: Account,
        account_id_value: str,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        client_send_lock: anyio.Lock,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
        proxy_request_budget_seconds: float,
        stream_idle_timeout_seconds: float,
    ) -> None:
        try:
            while True:
                receive_timeout = await self._next_websocket_receive_timeout(
                    pending_requests,
                    pending_lock=pending_lock,
                    proxy_request_budget_seconds=proxy_request_budget_seconds,
                    stream_idle_timeout_seconds=stream_idle_timeout_seconds,
                )
                try:
                    if receive_timeout is None:
                        message = await upstream.receive()
                    elif receive_timeout.timeout_seconds <= 0:
                        raise asyncio.TimeoutError()
                    else:
                        message = await asyncio.wait_for(
                            upstream.receive(),
                            timeout=receive_timeout.timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    if receive_timeout is None:
                        raise
                    if receive_timeout.fail_all_pending:
                        await self._fail_pending_websocket_requests(
                            account_id_value=account_id_value,
                            pending_requests=pending_requests,
                            pending_lock=pending_lock,
                            error_code=receive_timeout.error_code,
                            error_message=receive_timeout.error_message,
                            api_key=api_key,
                            websocket=websocket,
                            client_send_lock=client_send_lock,
                            response_create_gate=response_create_gate,
                        )
                        upstream_control.reconnect_requested = True
                        try:
                            await upstream.close()
                        except Exception:
                            logger.debug("Failed to close upstream websocket after timeout", exc_info=True)
                        break
                    await self._fail_expired_pending_websocket_requests(
                        account_id_value=account_id_value,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        request_budget_seconds=proxy_request_budget_seconds,
                        error_code=receive_timeout.error_code,
                        error_message=receive_timeout.error_message,
                        api_key=api_key,
                        websocket=websocket,
                        client_send_lock=client_send_lock,
                        response_create_gate=response_create_gate,
                    )
                    continue
                if message.kind == "text" and message.text is not None:
                    await self._process_upstream_websocket_text(
                        message.text,
                        account=account,
                        account_id_value=account_id_value,
                        pending_requests=pending_requests,
                        pending_lock=pending_lock,
                        api_key=api_key,
                        upstream_control=upstream_control,
                        response_create_gate=response_create_gate,
                    )
                    async with client_send_lock:
                        await websocket.send_text(message.text)
                    if upstream_control.reconnect_requested:
                        async with pending_lock:
                            should_reconnect = not pending_requests
                        if should_reconnect:
                            try:
                                await upstream.close()
                            except Exception:
                                logger.debug("Failed to close upstream websocket for reconnect", exc_info=True)
                            break
                    continue
                if message.kind == "binary" and message.data is not None:
                    async with client_send_lock:
                        await websocket.send_bytes(message.data)
                    continue
                await self._fail_pending_websocket_requests(
                    account_id_value=account_id_value,
                    pending_requests=pending_requests,
                    pending_lock=pending_lock,
                    error_code="stream_incomplete",
                    error_message=_upstream_websocket_disconnect_message(message),
                    api_key=api_key,
                    websocket=websocket,
                    client_send_lock=client_send_lock,
                    response_create_gate=response_create_gate,
                )
                break
        finally:
            async with pending_lock:
                has_pending_requests = bool(pending_requests)
            if upstream_control.reconnect_requested or not has_pending_requests:
                return
            try:
                await websocket.close()
            except Exception:
                logger.debug("Failed to close downstream websocket", exc_info=True)

    async def _process_upstream_websocket_text(
        self,
        text: str,
        *,
        account: Account,
        account_id_value: str,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        event_block = f"data: {text}\n\n"
        payload = parse_sse_data_json(event_block)
        event = parse_sse_event(event_block)
        event_type = _event_type_from_payload(event, payload)
        response_id = _websocket_response_id(event, payload)

        async with pending_lock:
            request_state = None
            created_request_state = None
            if event_type == "response.created":
                request_state = _assign_websocket_response_id(pending_requests, response_id)
                created_request_state = request_state
                release_create_gate = request_state is not None
            elif response_id is not None:
                request_state = _find_websocket_request_state_by_response_id(pending_requests, response_id)
                release_create_gate = False
            elif response_id is None and len(pending_requests) == 1:
                request_state = pending_requests[0]
                release_create_gate = False
            else:
                release_create_gate = False
            if request_state is not None:
                actual_service_tier = _service_tier_from_event_payload(payload)
                if actual_service_tier is not None:
                    request_state.service_tier = actual_service_tier
            if (
                event_type in {"response.completed", "response.failed", "response.incomplete", "error"}
                and pending_requests
            ):
                request_state = _pop_terminal_websocket_request_state(
                    pending_requests,
                    response_id=response_id,
                    fallback_request_state=request_state,
                )
            else:
                request_state = None

        if event_type == "response.created" and release_create_gate and created_request_state is not None:
            _release_websocket_response_create_gate(created_request_state, response_create_gate)

        if request_state is None:
            return

        await self._finalize_websocket_request_state(
            request_state,
            account=account,
            account_id_value=account_id_value,
            event=event,
            event_type=event_type,
            payload=payload,
            api_key=api_key,
            upstream_control=upstream_control,
            response_create_gate=response_create_gate,
        )

    async def _next_websocket_receive_timeout(
        self,
        pending_requests: deque[_WebSocketRequestState],
        *,
        pending_lock: anyio.Lock,
        proxy_request_budget_seconds: float,
        stream_idle_timeout_seconds: float,
    ) -> _WebSocketReceiveTimeout | None:
        async with pending_lock:
            started_ats = [request_state.started_at for request_state in pending_requests]
        return _websocket_receive_timeout_for_pending_requests(
            started_ats,
            proxy_request_budget_seconds=proxy_request_budget_seconds,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
        )

    async def _fail_expired_pending_websocket_requests(
        self,
        *,
        account_id_value: str | None,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        request_budget_seconds: float,
        error_code: str,
        error_message: str,
        api_key: ApiKeyData | None,
        websocket: WebSocket | None = None,
        client_send_lock: anyio.Lock | None = None,
        response_create_gate: asyncio.Semaphore | None = None,
    ) -> None:
        now = time.monotonic()
        async with pending_lock:
            expired_requests = [
                request_state
                for request_state in list(pending_requests)
                if now >= request_state.started_at + request_budget_seconds
            ]
            for request_state in expired_requests:
                pending_requests.remove(request_state)
        if not expired_requests:
            return
        await self._fail_pending_websocket_requests(
            account_id_value=account_id_value,
            pending_requests=deque(expired_requests),
            pending_lock=anyio.Lock(),
            error_code=error_code,
            error_message=error_message,
            api_key=api_key,
            websocket=websocket,
            client_send_lock=client_send_lock,
            response_create_gate=response_create_gate,
        )

    async def _finalize_websocket_request_state(
        self,
        request_state: _WebSocketRequestState,
        *,
        account: Account,
        account_id_value: str,
        event: OpenAIEvent | None,
        event_type: str | None,
        payload: dict[str, JsonValue] | None,
        api_key: ApiKeyData | None,
        upstream_control: _WebSocketUpstreamControl,
        response_create_gate: asyncio.Semaphore,
    ) -> None:
        status = "success"
        error_code = None
        error_message = None
        usage = None
        error_payload: UpstreamError | None = None
        response_id = request_state.response_id or request_state.request_id
        response_service_tier = request_state.service_tier

        if event_type == "error":
            status = "error"
            error = event.error if event else None
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            error_payload = _upstream_error_from_openai(error)
        elif event_type in {"response.failed", "response.incomplete"}:
            status = "error"
            error = event.response.error if event and event.response else None
            error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
            error_message = error.message if error else None
            if event_type == "response.failed":
                error_payload = _upstream_error_from_openai(error)
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id
        elif event_type == "response.completed":
            usage = event.response.usage if event and event.response else None
            if event and event.response and event.response.id:
                response_id = event.response.id

        actual_service_tier = _service_tier_from_event_payload(payload)
        if actual_service_tier is not None:
            response_service_tier = actual_service_tier

        settlement = _StreamSettlement(
            status=status,
            model=request_state.model or "",
            service_tier=response_service_tier,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_input_tokens=(
                usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
            ),
            error_code=error_code,
            error_message=error_message,
            error=error_payload,
        )
        if event_type in {"response.failed", "response.incomplete", "error"}:
            settlement.record_success = False
        if event_type in {"response.failed", "error"}:
            settlement.account_health_error = _should_penalize_stream_error(error_code)
        _release_websocket_response_create_gate(request_state, response_create_gate)
        await self._settle_stream_api_key_usage(
            api_key,
            request_state.api_key_reservation,
            settlement,
            response_id,
        )
        if settlement.account_health_error:
            await self._handle_stream_error(
                account,
                _stream_settlement_error_payload(settlement),
                settlement.error_code or "upstream_error",
            )
            upstream_control.reconnect_requested = True
        elif settlement.record_success:
            await self._load_balancer.record_success(account)

        latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
        cached_input_tokens = usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else None
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens if usage and usage.output_tokens_details else None
        )
        await self._write_request_log(
            account_id=account_id_value,
            api_key=api_key,
            request_id=response_id,
            model=request_state.model or "",
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            error_message=error_message,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_effort=request_state.reasoning_effort,
            transport=_REQUEST_TRANSPORT_WEBSOCKET,
            service_tier=response_service_tier,
        )

    async def _write_websocket_connect_failure(
        self,
        *,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        error_code: str,
        error_message: str,
    ) -> None:
        await self._write_request_log(
            account_id=account_id,
            api_key=api_key,
            request_id=request_state.request_id,
            model=request_state.model or "",
            latency_ms=int((time.monotonic() - request_state.started_at) * 1000),
            status="error",
            error_code=error_code,
            error_message=error_message,
            reasoning_effort=request_state.reasoning_effort,
            transport=_REQUEST_TRANSPORT_WEBSOCKET,
            service_tier=request_state.service_tier,
        )

    async def _emit_websocket_connect_failure(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
        status_code: int,
        payload: OpenAIErrorEnvelope,
        error_code: str,
        error_message: str,
    ) -> None:
        await self._release_websocket_reservation(request_state.api_key_reservation)
        await self._write_websocket_connect_failure(
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
            error_code=error_code,
            error_message=error_message,
        )
        async with client_send_lock:
            await websocket.send_text(
                _serialize_websocket_error_event(_wrapped_websocket_error_event(status_code, payload))
            )

    async def _emit_websocket_proxy_request_timeout(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_state: _WebSocketRequestState,
    ) -> None:
        await self._emit_websocket_connect_failure(
            websocket,
            client_send_lock=client_send_lock,
            account_id=account_id,
            api_key=api_key,
            request_state=request_state,
            status_code=502,
            payload=openai_error(
                "upstream_request_timeout",
                "Proxy request budget exhausted",
                error_type="server_error",
            ),
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )

    async def _fail_pending_websocket_requests(
        self,
        *,
        account_id_value: str | None,
        pending_requests: deque[_WebSocketRequestState],
        pending_lock: anyio.Lock,
        error_code: str,
        error_message: str,
        api_key: ApiKeyData | None,
        websocket: WebSocket | None = None,
        client_send_lock: anyio.Lock | None = None,
        response_create_gate: asyncio.Semaphore | None = None,
    ) -> None:
        async with pending_lock:
            remaining = list(pending_requests)
            pending_requests.clear()

        for request_state in remaining:
            if response_create_gate is not None:
                _release_websocket_response_create_gate(request_state, response_create_gate)
            if websocket is not None and client_send_lock is not None:
                await self._emit_websocket_terminal_error(
                    websocket,
                    client_send_lock=client_send_lock,
                    request_state=request_state,
                    error_code=error_code,
                    error_message=error_message,
                )
            await self._release_websocket_reservation(request_state.api_key_reservation)
            if account_id_value is None:
                continue
            latency_ms = int((time.monotonic() - request_state.started_at) * 1000)
            await self._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_state.response_id or request_state.request_id,
                model=request_state.model or "",
                latency_ms=latency_ms,
                status="error",
                error_code=error_code,
                error_message=error_message,
                reasoning_effort=request_state.reasoning_effort,
                transport=_REQUEST_TRANSPORT_WEBSOCKET,
                service_tier=request_state.service_tier,
            )

    async def _emit_websocket_terminal_error(
        self,
        websocket: WebSocket,
        *,
        client_send_lock: anyio.Lock,
        request_state: _WebSocketRequestState,
        error_code: str,
        error_message: str,
    ) -> None:
        event = response_failed_event(
            error_code,
            error_message,
            response_id=request_state.response_id or request_state.request_id,
        )
        try:
            async with client_send_lock:
                await websocket.send_text(json.dumps(event, ensure_ascii=True, separators=(",", ":")))
        except Exception:
            logger.debug("Failed to emit websocket terminal error", exc_info=True)

    async def _reserve_websocket_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        *,
        request_model: str | None,
        request_service_tier: str | None,
    ) -> ApiKeyUsageReservationData | None:
        if api_key is None:
            return None

        with anyio.CancelScope(shield=True):
            async with self._repo_factory() as repos:
                service = ApiKeysService(repos.api_keys)
                try:
                    return await service.enforce_limits_for_request(
                        api_key.id,
                        request_model=request_model,
                        request_service_tier=request_service_tier,
                    )
                except ApiKeyRateLimitExceededError as exc:
                    message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
                    raise ProxyRateLimitError(message) from exc
                except ApiKeyInvalidError as exc:
                    raise ProxyAuthError(str(exc)) from exc

    async def _release_websocket_reservation(
        self,
        reservation: ApiKeyUsageReservationData | None,
    ) -> None:
        if reservation is None:
            return
        with anyio.CancelScope(shield=True):
            async with self._repo_factory() as repos:
                service = ApiKeysService(repos.api_keys)
                await service.release_usage_reservation(reservation.reservation_id)

    async def _settle_compact_api_key_usage(
        self,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        response: CompactResponsePayload | None,
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

            # Fetch additional rate limits
            additional_rate_limits = await self._build_additional_rate_limits(repos, account_map, now_epoch)

            return RateLimitStatusPayloadData(
                plan_type=_plan_type_for_accounts(selected_accounts),
                rate_limit=_rate_limit_details(primary_window, secondary_window),
                credits=_credits_snapshot(await self._latest_usage_entries(repos, account_map)),
                additional_rate_limits=additional_rate_limits,
            )

    async def _stream_with_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        propagate_http_errors: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool,
        request_transport: str,
    ) -> AsyncIterator[str]:
        request_id = ensure_request_id()
        start = time.monotonic()
        base_settings = get_settings()
        settings = await get_settings_cache().get()
        deadline = start + base_settings.proxy_request_budget_seconds
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        affinity = _sticky_key_for_responses_request(
            payload,
            headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=settings.sticky_threads_enabled,
        )
        routing_strategy = _routing_strategy(settings)
        max_attempts = 3
        settled = False
        any_attempt_logged = False
        settlement = _StreamSettlement()
        try:
            for attempt in range(max_attempts):
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "Proxy request budget exhausted before retry request_id=%s attempt=%s",
                        request_id,
                        attempt + 1,
                    )
                    await self._write_stream_preflight_error(
                        account_id=None,
                        api_key=api_key,
                        request_id=request_id,
                        model=payload.model,
                        start=start,
                        error_code="upstream_request_timeout",
                        error_message="Proxy request budget exhausted",
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        service_tier=payload.service_tier,
                        transport=request_transport,
                    )
                    yield format_sse_event(_proxy_request_timeout_event(request_id))
                    return
                try:
                    selection = await self._select_account_with_budget(
                        deadline,
                        request_id=request_id,
                        kind="stream",
                        sticky_key=affinity.key,
                        sticky_kind=affinity.kind,
                        reallocate_sticky=affinity.reallocate_sticky,
                        sticky_max_age_seconds=affinity.max_age_seconds,
                        prefer_earlier_reset_accounts=prefer_earlier_reset,
                        routing_strategy=routing_strategy,
                        model=payload.model,
                    )
                except ProxyResponseError as exc:
                    error = _parse_openai_error(exc.payload)
                    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
                    error_message = error.message if error else None
                    if error_code == "upstream_unavailable" and error_message == "Proxy request budget exhausted":
                        await self._write_stream_preflight_error(
                            account_id=None,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                        )
                        yield format_sse_event(_proxy_request_timeout_event(request_id))
                        return
                    event = response_failed_event(
                        error_code,
                        error_message or "Upstream unavailable",
                        error_type=(error.type or "server_error") if error else "server_error",
                        response_id=request_id,
                    )
                    _apply_error_metadata(event["response"]["error"], error)
                    yield format_sse_event(event)
                    return
                account = selection.account
                if not account:
                    no_accounts_msg = selection.error_message or "No active accounts available"
                    error_code = selection.error_code or "no_accounts"
                    event = response_failed_event(
                        error_code,
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
                        error_code=error_code,
                        error_message=no_accounts_msg,
                        reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                        transport=request_transport,
                        service_tier=payload.service_tier,
                    )
                    return

                account_id_value = account.id
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "Proxy request budget exhausted before freshness check "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await self._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                        )
                        yield format_sse_event(_proxy_request_timeout_event(request_id))
                        return
                    try:
                        account = await self._ensure_fresh_with_budget(account, timeout_seconds=remaining_budget)
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        logger.warning(
                            "Stream refresh/connect failed request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                            exc_info=True,
                        )
                        message = str(exc) or "Request to upstream timed out"
                        await self._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_unavailable",
                            error_message=message,
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                        )
                        event = response_failed_event(
                            "upstream_unavailable",
                            message,
                            response_id=request_id,
                        )
                        yield format_sse_event(event)
                        return
                    any_attempt_logged = True
                    settlement = _StreamSettlement()
                    effective_attempt_timeout = _remaining_budget_seconds(deadline)
                    if effective_attempt_timeout <= 0:
                        logger.warning(
                            "Proxy request budget exhausted before stream attempt "
                            "request_id=%s attempt=%s account_id=%s",
                            request_id,
                            attempt + 1,
                            account.id,
                        )
                        await self._write_stream_preflight_error(
                            account_id=account.id,
                            api_key=api_key,
                            request_id=request_id,
                            model=payload.model,
                            start=start,
                            error_code="upstream_request_timeout",
                            error_message="Proxy request budget exhausted",
                            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                            service_tier=payload.service_tier,
                            transport=request_transport,
                        )
                        yield format_sse_event(_proxy_request_timeout_event(request_id))
                        return
                    stream_timeout_tokens = _push_stream_attempt_timeout_overrides(effective_attempt_timeout)
                    try:
                        async for line in self._stream_once(
                            account,
                            payload,
                            headers,
                            request_id,
                            attempt < max_attempts - 1,
                            api_key=api_key,
                            settlement=settlement,
                            suppress_text_done_events=suppress_text_done_events,
                            request_transport=request_transport,
                        ):
                            yield line
                    finally:
                        pop_stream_timeout_overrides(stream_timeout_tokens)
                    if settlement.account_health_error:
                        await self._handle_stream_error(
                            account,
                            _stream_settlement_error_payload(settlement),
                            settlement.error_code or "upstream_error",
                        )
                    elif settlement.record_success:
                        await self._load_balancer.record_success(account)
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
                except _TerminalStreamError as exc:
                    if _should_penalize_stream_error(exc.code):
                        await self._handle_stream_error(account, exc.error, exc.code)
                    return
                except ProxyResponseError as exc:
                    if exc.status_code == 401:
                        remaining_budget = _remaining_budget_seconds(deadline)
                        if remaining_budget <= 0:
                            logger.warning(
                                "Proxy request budget exhausted before forced refresh retry "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await self._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                            )
                            yield format_sse_event(_proxy_request_timeout_event(request_id))
                            return
                        try:
                            account = await self._ensure_fresh_with_budget(
                                account,
                                force=True,
                                timeout_seconds=remaining_budget,
                            )
                        except RefreshError as refresh_exc:
                            if refresh_exc.is_permanent:
                                await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                            continue
                        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                            logger.warning(
                                "Stream forced refresh/connect failed request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                                exc_info=True,
                            )
                            message = str(exc) or "Request to upstream timed out"
                            await self._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_unavailable",
                                error_message=message,
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                            )
                            event = response_failed_event(
                                "upstream_unavailable",
                                message,
                                response_id=request_id,
                            )
                            yield format_sse_event(event)
                            return
                        settlement = _StreamSettlement()
                        effective_attempt_timeout = _remaining_budget_seconds(deadline)
                        if effective_attempt_timeout <= 0:
                            logger.warning(
                                "Proxy request budget exhausted before post-refresh stream attempt "
                                "request_id=%s attempt=%s account_id=%s",
                                request_id,
                                attempt + 1,
                                account.id,
                            )
                            await self._write_stream_preflight_error(
                                account_id=account.id,
                                api_key=api_key,
                                request_id=request_id,
                                model=payload.model,
                                start=start,
                                error_code="upstream_request_timeout",
                                error_message="Proxy request budget exhausted",
                                reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                                service_tier=payload.service_tier,
                                transport=request_transport,
                            )
                            yield format_sse_event(_proxy_request_timeout_event(request_id))
                            return
                        stream_timeout_tokens = _push_stream_attempt_timeout_overrides(effective_attempt_timeout)
                        try:
                            async for line in self._stream_once(
                                account,
                                payload,
                                headers,
                                request_id,
                                False,
                                api_key=api_key,
                                settlement=settlement,
                                suppress_text_done_events=suppress_text_done_events,
                                request_transport=request_transport,
                            ):
                                yield line
                        finally:
                            pop_stream_timeout_overrides(stream_timeout_tokens)
                        if settlement.account_health_error:
                            await self._handle_stream_error(
                                account,
                                _stream_settlement_error_payload(settlement),
                                settlement.error_code or "upstream_error",
                            )
                        elif settlement.record_success:
                            await self._load_balancer.record_success(account)
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
                    if _should_penalize_stream_error(error_code):
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
                    logger.warning(
                        "Proxy streaming failed without retry account_id=%s request_id=%s",
                        account_id_value,
                        request_id,
                        exc_info=True,
                    )
                    event = response_failed_event(
                        "upstream_error",
                        "Proxy streaming failed",
                        response_id=request_id,
                    )
                    yield format_sse_event(event)
                    return
            retries_exhausted_msg = "No available accounts after retries"
            event = response_failed_event(
                "no_accounts",
                retries_exhausted_msg,
                response_id=request_id,
            )
            yield format_sse_event(event)
            if not any_attempt_logged:
                await self._write_request_log(
                    account_id=None,
                    api_key=api_key,
                    request_id=request_id,
                    model=payload.model,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="error",
                    error_code="no_accounts",
                    error_message=retries_exhausted_msg,
                    reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
                    transport=request_transport,
                    service_tier=payload.service_tier,
                )
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
        request_transport: str,
    ) -> AsyncIterator[str]:
        account_id_value = account.id
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        account_id = _header_account_id(account.chatgpt_account_id)
        model = payload.model
        requested_service_tier = payload.service_tier
        service_tier = requested_service_tier
        actual_service_tier: str | None = None
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
            event_service_tier = _service_tier_from_event_payload(first_payload)
            if event_service_tier is not None:
                actual_service_tier = event_service_tier
                service_tier = event_service_tier
            terminal_stream_error: _TerminalStreamError | None = None
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
                settlement.error = _upstream_error_from_openai(error)
                settlement.record_success = False
                settlement.account_health_error = _should_penalize_stream_error(code)
                if allow_retry and _should_retry_stream_error(code):
                    raise _RetryableStreamError(code, settlement.error)
                terminal_stream_error = _TerminalStreamError(
                    code,
                    settlement.error,
                )
                if allow_retry:
                    logger.info(
                        "Not retrying non-recoverable stream failure request_id=%s account_id=%s code=%s",
                        request_id,
                        account_id_value,
                        code,
                    )

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
            if terminal_stream_error is not None:
                raise terminal_stream_error

            async for line in iterator:
                event_payload = parse_sse_data_json(line)
                event = parse_sse_event(line)
                event_type = _event_type_from_payload(event, event_payload)
                event_service_tier = _service_tier_from_event_payload(event_payload)
                if event_service_tier is not None:
                    actual_service_tier = event_service_tier
                    service_tier = event_service_tier
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
                        settlement.error = _upstream_error_from_openai(error)
                        settlement.record_success = False
                        settlement.account_health_error = _should_penalize_stream_error(error_code)
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
            settlement.record_success = False
            settlement.account_health_error = _should_penalize_stream_error(error_code)
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
            settlement.error_code = error_code
            settlement.error_message = error_message
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
                transport=request_transport,
                service_tier=service_tier,
            )
            _maybe_log_proxy_service_tier_trace(
                "stream",
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
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
        transport: str | None = None,
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
                        transport=transport,
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

    async def _write_stream_preflight_error(
        self,
        *,
        account_id: str | None,
        api_key: ApiKeyData | None,
        request_id: str,
        model: str | None,
        start: float,
        error_code: str,
        error_message: str,
        reasoning_effort: str | None,
        service_tier: str | None,
        transport: str = _REQUEST_TRANSPORT_HTTP,
    ) -> None:
        await self._write_request_log(
            account_id=account_id,
            api_key=api_key,
            request_id=request_id,
            model=model,
            latency_ms=int((time.monotonic() - start) * 1000),
            status="error",
            error_code=error_code,
            error_message=error_message,
            reasoning_effort=reasoning_effort,
            transport=transport,
            service_tier=service_tier,
        )

    async def _refresh_usage(self, repos: ProxyRepositories, accounts: list[Account]) -> None:
        latest_usage = await repos.usage.latest_by_account(window="primary")
        updater = UsageUpdater(repos.usage, repos.accounts, repos.additional_usage)
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

    async def _build_additional_rate_limits(
        self,
        repos: ProxyRepositories,
        account_map: dict[str, Account],
        now_epoch: int,
    ) -> list[AdditionalRateLimitData]:
        """Build additional rate limit entries from AdditionalUsageRepository."""
        if not account_map:
            return []

        limit_names = await repos.additional_usage.list_limit_names(account_ids=list(account_map.keys()))
        additional_limits = []

        for limit_name in limit_names:
            # Fetch latest entries for this limit across all accounts
            latest_entries = await repos.additional_usage.latest_by_account(
                limit_name=limit_name,
                window="primary",
            )
            latest_secondary = await repos.additional_usage.latest_by_account(
                limit_name=limit_name,
                window="secondary",
            )

            # Filter to selected accounts
            filtered_entries = {
                account_id: entry for account_id, entry in latest_entries.items() if account_id in account_map
            }
            filtered_secondary = {
                account_id: entry for account_id, entry in latest_secondary.items() if account_id in account_map
            }

            if not filtered_entries and not filtered_secondary:
                continue

            first_entry = (
                next(iter(filtered_entries.values())) if filtered_entries else next(iter(filtered_secondary.values()))
            )
            metered_feature = first_entry.metered_feature

            window_snapshot = None
            avg_used_percent = None
            if filtered_entries:
                used_percents = [
                    entry.used_percent for entry in filtered_entries.values() if entry.used_percent is not None
                ]
                if used_percents:
                    avg_used_percent = sum(used_percents) / len(used_percents)
                    window_minutes_values = [e.window_minutes for e in filtered_entries.values() if e.window_minutes]
                    reset_at_values = [e.reset_at for e in filtered_entries.values() if e.reset_at is not None]

                    if window_minutes_values and reset_at_values:
                        window_minutes = max(window_minutes_values)
                        limit_window_seconds = int(window_minutes * 60)
                        reset_at = int(min(reset_at_values))
                        reset_after_seconds = max(0, reset_at - now_epoch)

                        window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, avg_used_percent))),
                            limit_window_seconds=limit_window_seconds,
                            reset_after_seconds=reset_after_seconds,
                            reset_at=reset_at,
                        )
                    else:
                        # Timing metadata absent — still emit used_percent
                        # so clients retain visibility into quota consumption.
                        window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, avg_used_percent))),
                        )

            secondary_window_snapshot = None
            if filtered_secondary:
                sec_used_percents = [e.used_percent for e in filtered_secondary.values() if e.used_percent is not None]
                if sec_used_percents:
                    sec_avg = sum(sec_used_percents) / len(sec_used_percents)
                    sec_window_values = [e.window_minutes for e in filtered_secondary.values() if e.window_minutes]
                    sec_reset_values = [e.reset_at for e in filtered_secondary.values() if e.reset_at is not None]

                    if sec_window_values and sec_reset_values:
                        sec_window_minutes = max(sec_window_values)
                        sec_limit_window_seconds = int(sec_window_minutes * 60)
                        sec_reset_at = int(min(sec_reset_values))
                        sec_reset_after_seconds = max(0, sec_reset_at - now_epoch)
                        secondary_window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, sec_avg))),
                            limit_window_seconds=sec_limit_window_seconds,
                            reset_after_seconds=sec_reset_after_seconds,
                            reset_at=sec_reset_at,
                        )
                    else:
                        secondary_window_snapshot = RateLimitWindowSnapshotData(
                            used_percent=int(max(0.0, min(100.0, sec_avg))),
                        )

            rate_limit_details = None
            if avg_used_percent is not None or secondary_window_snapshot is not None:
                # Per-account availability: an account is available when
                # neither its primary nor secondary window is exhausted.
                # Pool is allowed when at least one account can serve.
                all_account_ids = set(filtered_entries.keys()) | set(filtered_secondary.keys())
                any_available = False
                for aid in all_account_ids:
                    pri_pct = filtered_entries[aid].used_percent if aid in filtered_entries else 0.0
                    sec_pct = filtered_secondary[aid].used_percent if aid in filtered_secondary else 0.0
                    if pri_pct < 100.0 and sec_pct < 100.0:
                        any_available = True
                        break
                rate_limit_details = RateLimitStatusDetailsData(
                    allowed=any_available,
                    limit_reached=not any_available,
                    primary_window=window_snapshot,
                    secondary_window=secondary_window_snapshot,
                )

            additional_limits.append(
                AdditionalRateLimitData(
                    quota_key=limit_name,
                    limit_name=first_entry.limit_name,
                    display_label=get_additional_display_label_for_quota_key(limit_name) or first_entry.limit_name,
                    metered_feature=metered_feature,
                    rate_limit=rate_limit_details,
                )
            )

        return additional_limits

    async def _ensure_fresh(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> Account:
        async with self._repo_factory() as repos:
            auth_manager = AuthManager(repos.accounts)
            token = push_token_refresh_timeout_override(timeout_seconds)
            try:
                return await auth_manager.ensure_fresh(account, force=force)
            finally:
                pop_token_refresh_timeout_override(token)

    async def _ensure_fresh_with_budget(
        self,
        account: Account,
        *,
        force: bool = False,
        timeout_seconds: float | None = None,
    ) -> Account:
        parameters = inspect.signature(self._ensure_fresh).parameters
        if "timeout_seconds" in parameters:
            return await self._ensure_fresh(account, force=force, timeout_seconds=timeout_seconds)
        return await self._ensure_fresh(account, force=force)

    async def _select_account_with_budget(
        self,
        deadline: float,
        *,
        request_id: str,
        kind: str,
        sticky_key: str | None = None,
        sticky_kind: StickySessionKind | None = None,
        reallocate_sticky: bool = False,
        sticky_max_age_seconds: int | None = None,
        prefer_earlier_reset_accounts: bool = False,
        routing_strategy: RoutingStrategy = "usage_weighted",
        model: str | None = None,
        additional_limit_name: str | None = None,
    ) -> AccountSelection:
        remaining_budget = _remaining_budget_seconds(deadline)
        if remaining_budget <= 0:
            logger.warning(
                "%s request budget exhausted before account selection request_id=%s", kind.title(), request_id
            )
            _raise_proxy_budget_exhausted()
        try:
            with anyio.fail_after(remaining_budget):
                return await self._load_balancer.select_account(
                    sticky_key=sticky_key,
                    sticky_kind=sticky_kind,
                    reallocate_sticky=reallocate_sticky,
                    sticky_max_age_seconds=sticky_max_age_seconds,
                    prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
                    routing_strategy=routing_strategy,
                    model=model,
                    additional_limit_name=additional_limit_name,
                )
        except TimeoutError:
            logger.warning("%s account selection exceeded request budget request_id=%s", kind.title(), request_id)
            _raise_proxy_budget_exhausted()

    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None:
        error = _parse_openai_error(exc.payload)
        code = _normalize_error_code(
            error.code if error else None,
            error.type if error else None,
        )
        await self._handle_stream_error(
            account,
            _upstream_error_from_openai(error),
            code,
        )

    async def _handle_stream_error(
        self,
        account: Account,
        error: UpstreamError,
        code: str,
    ) -> None:
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
        logger.info(
            "Recorded transient account error account_id=%s request_id=%s code=%s",
            account.id,
            get_request_id(),
            code,
        )


class _RetryableStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError) -> None:
        super().__init__(code)
        self.code = code
        self.error = error


class _TerminalStreamError(Exception):
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
    error_code: str | None = None
    error_message: str | None = None
    error: UpstreamError | None = None
    account_health_error: bool = False
    record_success: bool = True


def _stream_settlement_error_payload(settlement: _StreamSettlement) -> UpstreamError:
    if settlement.error is not None:
        return settlement.error
    payload: UpstreamError = {}
    if settlement.error_message:
        payload["message"] = settlement.error_message
    else:
        payload["message"] = "Upstream error"
    return payload


def _should_penalize_stream_error(code: str | None) -> bool:
    if code is None:
        return False
    return code in _ACCOUNT_RECOVERY_RETRY_CODES


@dataclass
class _WebSocketRequestState:
    request_id: str
    model: str | None
    service_tier: str | None
    reasoning_effort: str | None
    api_key_reservation: ApiKeyUsageReservationData | None
    started_at: float
    response_id: str | None = None
    awaiting_response_created: bool = False


@dataclass(slots=True)
class _WebSocketUpstreamControl:
    reconnect_requested: bool = False


@dataclass(slots=True)
class _PreparedWebSocketRequest:
    text_data: str
    request_state: _WebSocketRequestState
    affinity_policy: _AffinityPolicy


@dataclass(frozen=True, slots=True)
class _WebSocketReceiveTimeout:
    timeout_seconds: float
    error_code: str
    error_message: str
    fail_all_pending: bool = False


def _event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    return None


def _websocket_response_id(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None and event.response is not None and event.response.id:
        return event.response.id
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    response_id = response.get("id")
    if not isinstance(response_id, str):
        return None
    stripped = response_id.strip()
    return stripped or None


def _find_websocket_request_state_by_response_id(
    pending_requests: deque[_WebSocketRequestState],
    response_id: str,
) -> _WebSocketRequestState | None:
    for request_state in pending_requests:
        if request_state.response_id == response_id:
            return request_state
    return None


def _assign_websocket_response_id(
    pending_requests: deque[_WebSocketRequestState],
    response_id: str | None,
) -> _WebSocketRequestState | None:
    if response_id is None:
        return None
    existing = _find_websocket_request_state_by_response_id(pending_requests, response_id)
    if existing is not None:
        return existing
    for request_state in pending_requests:
        if request_state.response_id is None:
            request_state.response_id = response_id
            return request_state
    return None


def _release_websocket_response_create_gate(
    request_state: _WebSocketRequestState,
    response_create_gate: asyncio.Semaphore,
) -> None:
    if not request_state.awaiting_response_created:
        return
    request_state.awaiting_response_created = False
    response_create_gate.release()


def _pop_terminal_websocket_request_state(
    pending_requests: deque[_WebSocketRequestState],
    *,
    response_id: str | None,
    fallback_request_state: _WebSocketRequestState | None,
) -> _WebSocketRequestState | None:
    if response_id is not None:
        request_state = _find_websocket_request_state_by_response_id(pending_requests, response_id)
        if request_state is not None:
            pending_requests.remove(request_state)
            return request_state
    if fallback_request_state is not None and fallback_request_state in pending_requests:
        pending_requests.remove(fallback_request_state)
        return fallback_request_state
    unresolved_requests = [request_state for request_state in pending_requests if request_state.response_id is None]
    if len(unresolved_requests) == 1:
        request_state = unresolved_requests[0]
        pending_requests.remove(request_state)
        return request_state
    if response_id is None and len(pending_requests) == 1:
        return pending_requests.popleft()
    return None


def _upstream_websocket_disconnect_message(message: UpstreamWebSocketMessage) -> str:
    if message.kind == "error" and message.error:
        return f"Upstream websocket closed before response.completed: {message.error}"
    if message.close_code is not None:
        return f"Upstream websocket closed before response.completed (close_code={message.close_code})"
    return "Upstream websocket closed before response.completed"


def _websocket_receive_timeout_for_pending_requests(
    started_ats: Sequence[float],
    *,
    proxy_request_budget_seconds: float,
    stream_idle_timeout_seconds: float,
) -> _WebSocketReceiveTimeout | None:
    if not started_ats:
        return None

    idle_timeout_seconds = max(0.001, stream_idle_timeout_seconds)
    oldest_started_at = min(started_ats)
    remaining_budget = _remaining_budget_seconds(oldest_started_at + proxy_request_budget_seconds)

    if remaining_budget <= 0:
        return _WebSocketReceiveTimeout(
            timeout_seconds=0.0,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )
    if idle_timeout_seconds <= remaining_budget:
        return _WebSocketReceiveTimeout(
            timeout_seconds=idle_timeout_seconds,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
            fail_all_pending=True,
        )
    if remaining_budget <= idle_timeout_seconds:
        return _WebSocketReceiveTimeout(
            timeout_seconds=remaining_budget,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )
    return _WebSocketReceiveTimeout(
        timeout_seconds=idle_timeout_seconds,
        error_code="stream_idle_timeout",
        error_message="Upstream stream idle timeout",
        fail_all_pending=True,
    )


def _routing_strategy(settings: DashboardSettings) -> RoutingStrategy:
    value = settings.routing_strategy or "usage_weighted"
    return "round_robin" if value == "round_robin" else "usage_weighted"


def _parse_websocket_payload(text: str) -> dict[str, JsonValue] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _is_websocket_response_create(payload: dict[str, JsonValue]) -> bool:
    payload_type = payload.get("type")
    return isinstance(payload_type, str) and payload_type == "response.create"


def _app_error_to_websocket_event(exc: AppError) -> dict[str, JsonValue]:
    return _wrapped_websocket_error_event(
        exc.status_code,
        openai_error(exc.code, exc.message, error_type=getattr(exc, "error_type", "server_error")),
    )


def _wrapped_websocket_error_event(
    status_code: int,
    payload: OpenAIErrorEnvelope,
) -> dict[str, JsonValue]:
    event: dict[str, JsonValue] = {
        "type": "error",
        "status": status_code,
        "error": dict(payload["error"]),
    }
    return event


def _serialize_websocket_error_event(payload: dict[str, JsonValue]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _remaining_budget_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _websocket_connect_deadline(request_state: _WebSocketRequestState, budget_seconds: float) -> float:
    started_at = request_state.started_at if request_state.started_at > 0 else time.monotonic()
    return started_at + budget_seconds


def _push_stream_attempt_timeout_overrides(
    timeout_seconds: float,
) -> tuple[float | None, float | None, float | None]:
    return push_stream_timeout_overrides(
        connect_timeout_seconds=timeout_seconds,
        idle_timeout_seconds=timeout_seconds,
        total_timeout_seconds=timeout_seconds,
    )


def _proxy_request_timeout_event(request_id: str) -> ResponseFailedEvent:
    return response_failed_event(
        "upstream_request_timeout",
        "Proxy request budget exhausted",
        response_id=request_id,
    )


def _should_retry_stream_error(code: str) -> bool:
    return code in _ACCOUNT_RECOVERY_RETRY_CODES


def _raise_proxy_budget_exhausted() -> NoReturn:
    raise ProxyResponseError(
        502,
        openai_error("upstream_unavailable", "Proxy request budget exhausted"),
    )


def _raise_proxy_unavailable(message: str) -> NoReturn:
    raise ProxyResponseError(
        502,
        openai_error("upstream_unavailable", message),
    )


def _is_proxy_budget_exhausted_error(exc: ProxyResponseError) -> bool:
    error = _parse_openai_error(exc.payload)
    error_code = _normalize_error_code(error.code if error else None, error.type if error else None)
    error_message = error.message if error else None
    return error_code == "upstream_unavailable" and error_message == "Proxy request budget exhausted"


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
    prompt_cache_key = _prompt_cache_key_from_request_model(payload)
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


def _maybe_log_proxy_service_tier_trace(
    kind: str,
    *,
    requested_service_tier: str | None,
    actual_service_tier: str | None,
) -> None:
    settings = get_settings()
    if not settings.log_proxy_service_tier_trace:
        return

    logger.warning(
        "proxy_service_tier_trace request_id=%s kind=%s requested_service_tier=%s actual_service_tier=%s",
        get_request_id(),
        kind,
        requested_service_tier,
        actual_service_tier,
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


def _prompt_cache_key_from_request_model(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    typed_value = getattr(payload, "prompt_cache_key", None)
    if isinstance(typed_value, str) and typed_value:
        return typed_value
    if not payload.model_extra:
        return None
    extra_value = payload.model_extra.get("prompt_cache_key")
    if isinstance(extra_value, str) and extra_value:
        return extra_value
    camel_value = payload.model_extra.get("promptCacheKey")
    if isinstance(camel_value, str) and camel_value:
        return camel_value
    return None


def _sticky_key_from_payload(payload: ResponsesRequest) -> str | None:
    value = _prompt_cache_key_from_request_model(payload)
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
    openai_cache_affinity: bool,
    openai_cache_affinity_max_age_seconds: int,
    sticky_threads_enabled: bool,
) -> _AffinityPolicy:
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return _AffinityPolicy(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
            )
    if openai_cache_affinity:
        return _AffinityPolicy(
            key=_sticky_key_from_payload(payload),
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    if sticky_threads_enabled:
        return _AffinityPolicy(
            key=_sticky_key_from_payload(payload),
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
        )
    return _AffinityPolicy()


def _sticky_key_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    value = _prompt_cache_key_from_request_model(payload)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_for_compact_request(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    openai_cache_affinity_max_age_seconds: int,
    sticky_threads_enabled: bool,
) -> _AffinityPolicy:
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return _AffinityPolicy(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
            )
    if openai_cache_affinity:
        return _AffinityPolicy(
            key=_sticky_key_from_compact_payload(payload),
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    if sticky_threads_enabled:
        return _AffinityPolicy(
            key=_sticky_key_from_compact_payload(payload),
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
        )
    return _AffinityPolicy()


def _service_tier_from_compact_payload(payload: ResponsesCompactRequest) -> str | None:
    if not payload.model_extra:
        return None
    return _normalize_service_tier_value(payload.model_extra.get("service_tier"))


def _service_tier_from_response(
    response: OpenAIResponsePayload | CompactResponsePayload | None,
) -> str | None:
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
