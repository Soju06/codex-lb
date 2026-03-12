from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import AsyncIterator, Mapping, NoReturn

import aiohttp
import anyio

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
from app.core.clients.proxy import (
    stream_responses as core_stream_responses,
)
from app.core.clients.proxy import (
    transcribe_audio as core_transcribe_audio,
)
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.errors import ResponseFailedEvent, openai_error, response_failed_event
from app.core.openai.models import OpenAIEvent, OpenAIResponsePayload
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.core.usage.types import UsageWindowRow
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import Account, DashboardSettings, StickySessionKind, UsageHistory
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
from app.modules.proxy.load_balancer import AccountSelection, LoadBalancer
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.repo_bundle import ProxyRepoFactory, ProxyRepositories
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
    ) -> OpenAIResponsePayload:
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
        response: OpenAIResponsePayload | None = None
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

            async def _call_compact(target: Account) -> OpenAIResponsePayload:
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
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "Compact request budget exhausted before forced refresh retry request_id=%s account_id=%s",
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


def _event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    return None


def _routing_strategy(settings: DashboardSettings) -> RoutingStrategy:
    value = settings.routing_strategy or "usage_weighted"
    return "round_robin" if value == "round_robin" else "usage_weighted"


def _remaining_budget_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _push_stream_attempt_timeout_overrides(
    timeout_seconds: float,
) -> tuple[float | None, float | None, float | None]:
    return push_stream_timeout_overrides(
        connect_timeout_seconds=timeout_seconds,
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
