from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol, cast

import anyio

from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger("app.modules.proxy.service")

_REQUEST_TRANSPORT_HTTP = "http"


class _RequestLogServiceProtocol(Protocol):
    _repo_factory: ProxyRepoFactory
    _request_log_tasks: set[asyncio.Task[None]]


def _normalize_session_id(session_id: str | None) -> str | None:
    if not isinstance(session_id, str):
        return None
    stripped = session_id.strip()
    return stripped or None


class _RequestLogMixin:
    async def rewrite_request_log_model(self, request_id: str, model: str) -> None:
        """Override the ``model`` field on any ``request_logs`` row that
        matches ``request_id``.

        Used by route adapters that translate a public request shape
        (currently ``/v1/images/*``) into an internal Responses request: the
        first-pass log row stores the internal host model the proxy used
        for routing, and we rewrite it here once the public effective model
        is known so dashboards and usage views surface the user-visible
        ``gpt-image-*`` model instead of the host (e.g. ``gpt-5.5``).

        The upstream ``stream_responses`` generator writes its request_log
        row from a ``finally`` block that runs after the last chunk is
        yielded, which can race with the call site here. We therefore retry
        a few times with short backoff while the row is still missing.
        """
        if not request_id or not model:
            return
        proxy = cast(_RequestLogServiceProtocol, self)
        with anyio.CancelScope(shield=True):
            try:
                rowcount = 0
                # Total wait: 0 + 50 + 100 + 200 + 400 + 800 ms = 1550 ms.
                for delay in (0.0, 0.05, 0.1, 0.2, 0.4, 0.8):
                    if delay > 0:
                        await asyncio.sleep(delay)
                    async with proxy._repo_factory() as repos:
                        rowcount = await repos.request_logs.update_model_for_request(request_id, model)
                    if rowcount:
                        break
                if not rowcount:
                    logger.warning(
                        "rewrite_request_log_model: request_log row for %s never appeared; "
                        "public effective model %s not recorded",
                        request_id,
                        model,
                    )
            except Exception:
                logger.warning(
                    "failed to rewrite request_log model request_id=%s model=%s",
                    request_id,
                    model,
                    exc_info=True,
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
        latency_first_token_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        transport: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        session_id: str | None = None,
        failure_phase: str | None = None,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        bridge_stage: str | None = None,
        request_kind: str = "normal",
        upstream_proxy_route_mode: str | None = None,
        upstream_proxy_pool_id: str | None = None,
        upstream_proxy_endpoint_id: str | None = None,
        upstream_proxy_fallback_used: bool | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._persist_request_log(
                account_id=account_id,
                api_key_id=api_key.id if api_key else None,
                request_id=request_id,
                model=model,
                latency_ms=latency_ms,
                status=status,
                latency_first_token_ms=latency_first_token_ms,
                error_code=error_code,
                error_message=error_message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=reasoning_effort,
                transport=transport,
                service_tier=service_tier,
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
                session_id=session_id,
                failure_phase=failure_phase,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                upstream_status_code=upstream_status_code,
                upstream_error_code=upstream_error_code,
                bridge_stage=bridge_stage,
                request_kind=request_kind,
                upstream_proxy_route_mode=upstream_proxy_route_mode,
                upstream_proxy_pool_id=upstream_proxy_pool_id,
                upstream_proxy_endpoint_id=upstream_proxy_endpoint_id,
                upstream_proxy_fallback_used=upstream_proxy_fallback_used,
                upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
                useragent=useragent,
                useragent_group=useragent_group,
            ),
            name=f"proxy-request-log-{request_id}",
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            self._track_request_log_task(task, account_id=account_id, request_id=request_id)
            raise

    def _track_request_log_task(
        self,
        task: asyncio.Task[None],
        *,
        account_id: str | None,
        request_id: str,
    ) -> None:
        proxy = cast(_RequestLogServiceProtocol, self)
        proxy._request_log_tasks.add(task)

        def _request_log_done(done_task: asyncio.Task[None]) -> None:
            proxy._request_log_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                logger.warning(
                    "Request log persistence task cancelled account_id=%s request_id=%s",
                    account_id,
                    request_id,
                )
            except Exception as exc:
                logger.warning(
                    "Request log persistence task failed account_id=%s request_id=%s",
                    account_id,
                    request_id,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_request_log_done)

    async def _persist_request_log(
        self,
        *,
        account_id: str | None,
        api_key_id: str | None,
        request_id: str,
        model: str | None,
        latency_ms: int,
        status: str,
        latency_first_token_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        transport: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        session_id: str | None = None,
        failure_phase: str | None = None,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        bridge_stage: str | None = None,
        request_kind: str = "normal",
        upstream_proxy_route_mode: str | None = None,
        upstream_proxy_pool_id: str | None = None,
        upstream_proxy_endpoint_id: str | None = None,
        upstream_proxy_fallback_used: bool | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
    ) -> None:
        proxy = cast(_RequestLogServiceProtocol, self)
        try:
            async with proxy._repo_factory() as repos:
                await repos.request_logs.add_log(
                    account_id=account_id,
                    api_key_id=api_key_id,
                    session_id=_normalize_session_id(session_id),
                    request_id=request_id,
                    model=model or "",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    reasoning_tokens=reasoning_tokens,
                    reasoning_effort=reasoning_effort,
                    transport=transport,
                    service_tier=service_tier,
                    requested_service_tier=requested_service_tier,
                    actual_service_tier=actual_service_tier,
                    request_kind=request_kind,
                    latency_ms=latency_ms,
                    latency_first_token_ms=latency_first_token_ms,
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    failure_phase=failure_phase,
                    failure_detail=failure_detail,
                    failure_exception_type=failure_exception_type,
                    upstream_status_code=upstream_status_code,
                    upstream_error_code=upstream_error_code,
                    bridge_stage=bridge_stage,
                    upstream_proxy_route_mode=upstream_proxy_route_mode,
                    upstream_proxy_pool_id=upstream_proxy_pool_id,
                    upstream_proxy_endpoint_id=upstream_proxy_endpoint_id,
                    upstream_proxy_fallback_used=upstream_proxy_fallback_used,
                    upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
                    useragent=useragent,
                    useragent_group=useragent_group,
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
        upstream_proxy_fail_closed_reason: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
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
            requested_service_tier=service_tier,
            upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
            useragent=useragent,
            useragent_group=useragent_group,
        )
