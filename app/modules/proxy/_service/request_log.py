from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol, cast

import anyio

from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, proxy_phase_latency_seconds
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.affinity import _extract_model_class
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger("app.modules.proxy.service")

# _background_cleanup_tasks also tracks non-persistence work (bridge session
# close cleanups, security-work error forwards); the shutdown drain must not
# spend its budget on those - they have their own teardown - so it only waits
# on tasks whose names mark them as request-log or settlement persistence.
_PERSISTENCE_TASK_NAME_PREFIXES = (
    "proxy-request-log-",
    "proxy-stream-api-key-settle-",
    "proxy-release_stream_api_key_reservation",
)


def _is_persistence_task(task: asyncio.Task[None]) -> bool:
    return task.get_name().startswith(_PERSISTENCE_TASK_NAME_PREFIXES)


_REQUEST_TRANSPORT_HTTP = "http"


def _record_proxy_phase_latency(
    *,
    phase: str,
    latency_ms: int | None,
    transport: str | None,
    upstream_transport: str | None,
    useragent_group: str | None,
    model: str | None,
) -> None:
    del useragent_group
    if latency_ms is None or latency_ms < 0:
        return
    if not PROMETHEUS_AVAILABLE or proxy_phase_latency_seconds is None:
        return
    proxy_phase_latency_seconds.labels(
        phase=phase,
        transport=transport or "unknown",
        upstream_transport=upstream_transport or "unknown",
        model_class=_extract_model_class(model) if model else "unknown",
    ).observe(latency_ms / 1000.0)


class _RequestLogServiceProtocol(Protocol):
    _repo_factory: ProxyRepoFactory
    _request_log_tasks: set[asyncio.Task[None]]
    _background_cleanup_tasks: set[asyncio.Task[None]]


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

        The rewrite is persistence, not response work: it runs as a tracked
        background task (drained at shutdown alongside the log inserts), so
        image responses never wait on log durability. Inside the task we
        first await this request's pending detached insert, then retry with
        short backoff while the row is still missing (the upstream
        ``stream_responses`` generator writes its row from a ``finally``
        block that can race with the call site here).
        """
        if not request_id or not model:
            return
        task = asyncio.create_task(
            self._rewrite_request_log_model_once(request_id, model),
            name=f"proxy-request-log-rewrite-{request_id}",
        )
        self._track_request_log_task(task, account_id=None, request_id=request_id)

    async def _rewrite_request_log_model_once(self, request_id: str, model: str) -> None:
        proxy = cast(_RequestLogServiceProtocol, self)
        insert_task_name = f"proxy-request-log-{request_id}"
        with anyio.CancelScope(shield=True):
            try:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 30
                rowcount = 0
                delay = 0.0
                while True:
                    # Re-check for this request's insert task every iteration:
                    # the stream generator's finally can schedule the insert
                    # on a later event-loop turn than this rewrite task, so a
                    # one-time snapshot could miss it and fall back to blind
                    # polling against a DB-gated insert.
                    for pending_insert in [
                        task
                        for task in proxy._request_log_tasks
                        if task.get_name() == insert_task_name and not task.done()
                    ]:
                        remaining = max(0.1, deadline - loop.time())
                        try:
                            await asyncio.wait_for(asyncio.shield(pending_insert), timeout=remaining)
                        except Exception:  # insert failures surface via the update probe below
                            pass
                    async with proxy._repo_factory() as repos:
                        rowcount = await repos.request_logs.update_model_for_request(request_id, model)
                    if rowcount:
                        break
                    if loop.time() >= deadline:
                        logger.warning(
                            "rewrite_request_log_model: request_log row for %s never appeared; "
                            "public effective model %s not recorded",
                            request_id,
                            model,
                        )
                        break
                    delay = min(delay + 0.05, 0.8)
                    await asyncio.sleep(delay)
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
        latency_queue_ms: int | None = None,
        latency_response_created_ms: int | None = None,
        latency_first_upstream_event_ms: int | None = None,
        latency_response_create_gate_wait_ms: int | None = None,
        latency_bridge_queue_wait_ms: int | None = None,
        prewarm_status: str | None = None,
        prewarm_latency_ms: int | None = None,
        session_previous_gap_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        transport: str | None = None,
        upstream_transport: str | None = None,
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
        conversation_id: str | None = None,
        client_ip: str | None = None,
        archive_request_id: str | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._persist_request_log(
                account_id=account_id,
                api_key_id=api_key.id if api_key else None,
                request_id=request_id,
                archive_request_id=archive_request_id,
                model=model,
                latency_ms=latency_ms,
                status=status,
                latency_first_token_ms=latency_first_token_ms,
                latency_queue_ms=latency_queue_ms,
                latency_response_created_ms=latency_response_created_ms,
                latency_first_upstream_event_ms=latency_first_upstream_event_ms,
                latency_response_create_gate_wait_ms=latency_response_create_gate_wait_ms,
                latency_bridge_queue_wait_ms=latency_bridge_queue_wait_ms,
                prewarm_status=prewarm_status,
                prewarm_latency_ms=prewarm_latency_ms,
                session_previous_gap_ms=session_previous_gap_ms,
                error_code=error_code,
                error_message=error_message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=reasoning_effort,
                transport=transport,
                upstream_transport=upstream_transport,
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
                conversation_id=conversation_id,
                client_ip=client_ip,
            ),
            name=f"proxy-request-log-{request_id}",
        )
        # Detach unconditionally: the row is observational (dashboards, usage
        # aggregation) and nothing on the response path reads it back
        # synchronously — the one post-hoc consumer, the images model
        # rewrite, already retries while the row is missing. Awaiting the
        # INSERT+COMMIT here made every stream's close wait on a DB write,
        # and Codex CLI does not continue until the stream closes. Failures
        # are logged by the tracking callback, and shutdown drains the task
        # set (ProxyService.drain_persistence_tasks).
        self._track_request_log_task(task, account_id=account_id, request_id=request_id)
        _record_proxy_phase_latency(
            phase="ttft",
            latency_ms=latency_first_token_ms,
            transport=transport,
            upstream_transport=upstream_transport,
            useragent_group=useragent_group,
            model=model,
        )
        _record_proxy_phase_latency(
            phase="response_created",
            latency_ms=latency_response_created_ms,
            transport=transport,
            upstream_transport=upstream_transport,
            useragent_group=useragent_group,
            model=model,
        )
        _record_proxy_phase_latency(
            phase="first_upstream_event",
            latency_ms=latency_first_upstream_event_ms,
            transport=transport,
            upstream_transport=upstream_transport,
            useragent_group=useragent_group,
            model=model,
        )
        _record_proxy_phase_latency(
            phase="response_create_gate_wait",
            latency_ms=latency_response_create_gate_wait_ms,
            transport=transport,
            upstream_transport=upstream_transport,
            useragent_group=useragent_group,
            model=model,
        )
        _record_proxy_phase_latency(
            phase="bridge_queue_wait",
            latency_ms=latency_bridge_queue_wait_ms,
            transport=transport,
            upstream_transport=upstream_transport,
            useragent_group=useragent_group,
            model=model,
        )

    async def drain_persistence_tasks(self, timeout_seconds: float) -> bool:
        """Await detached request-log and settlement tasks, e.g. at shutdown.

        Persistence runs detached from the response path, so a graceful
        shutdown must flush whatever is still in flight or the final
        requests' logs and reservation settlements would be lost. Task done
        callbacks can schedule follow-up work (a failed settlement enqueues
        its reservation release), so draining loops until the tracked sets
        are stable rather than snapshotting once. Returns True when
        everything drained within the timeout.
        """
        proxy = cast(_RequestLogServiceProtocol, self)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            pending = {
                task
                for task in (proxy._request_log_tasks | proxy._background_cleanup_tasks)
                if not task.done() and _is_persistence_task(task)
            }
            if not pending:
                # One scheduling tick so just-finished tasks' done callbacks
                # (which may enqueue follow-up tasks) run before we re-check.
                await asyncio.sleep(0)
                if not any(
                    _is_persistence_task(task)
                    for task in (proxy._request_log_tasks | proxy._background_cleanup_tasks)
                    if not task.done()
                ):
                    return True
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                for task in pending:
                    logger.warning("Persistence task did not drain before shutdown: %s", task.get_name())
                return False
            done, still_pending = await asyncio.wait(pending, timeout=remaining)
            del done
            if still_pending:
                for task in still_pending:
                    logger.warning("Persistence task did not drain before shutdown: %s", task.get_name())
                return False

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
        archive_request_id: str | None,
        model: str | None,
        latency_ms: int,
        status: str,
        latency_first_token_ms: int | None = None,
        latency_queue_ms: int | None = None,
        latency_response_created_ms: int | None = None,
        latency_first_upstream_event_ms: int | None = None,
        latency_response_create_gate_wait_ms: int | None = None,
        latency_bridge_queue_wait_ms: int | None = None,
        prewarm_status: str | None = None,
        prewarm_latency_ms: int | None = None,
        session_previous_gap_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        transport: str | None = None,
        upstream_transport: str | None = None,
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
        conversation_id: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        proxy = cast(_RequestLogServiceProtocol, self)
        try:
            async with proxy._repo_factory() as repos:
                await repos.request_logs.add_log(
                    account_id=account_id,
                    api_key_id=api_key_id,
                    session_id=_normalize_session_id(session_id),
                    request_id=request_id,
                    archive_request_id=archive_request_id,
                    model=model or "",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    reasoning_tokens=reasoning_tokens,
                    reasoning_effort=reasoning_effort,
                    transport=transport,
                    upstream_transport=upstream_transport,
                    service_tier=service_tier,
                    requested_service_tier=requested_service_tier,
                    actual_service_tier=actual_service_tier,
                    request_kind=request_kind,
                    latency_ms=latency_ms,
                    latency_first_token_ms=latency_first_token_ms,
                    latency_queue_ms=latency_queue_ms,
                    latency_response_created_ms=latency_response_created_ms,
                    latency_first_upstream_event_ms=latency_first_upstream_event_ms,
                    latency_response_create_gate_wait_ms=latency_response_create_gate_wait_ms,
                    latency_bridge_queue_wait_ms=latency_bridge_queue_wait_ms,
                    prewarm_status=prewarm_status,
                    prewarm_latency_ms=prewarm_latency_ms,
                    session_previous_gap_ms=session_previous_gap_ms,
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
                    conversation_id=conversation_id,
                    client_ip=client_ip,
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
        upstream_transport: str | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
        conversation_id: str | None = None,
        client_ip: str | None = None,
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
            upstream_transport=upstream_transport,
            service_tier=service_tier,
            requested_service_tier=service_tier,
            upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
            useragent=useragent,
            useragent_group=useragent_group,
            conversation_id=conversation_id,
            client_ip=client_ip,
        )
