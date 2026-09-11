from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy import func, select

from app.core.scheduling.leader_election_handle import get_leader_election as _get_leader_election
from app.core.utils.time import utcnow
from app.db.models import ModelSource, ModelSourceModel, RequestLog
from app.db.session import detach_session_objects, get_background_session
from app.modules.model_sources.catalog import source_model_cost_usd
from app.modules.model_sources.forwarding import ModelSourceForwardingError, forward_responses
from app.modules.model_sources.governance import (
    COMPANY_HEALTH_CHECK_SOURCE,
    COMPANY_KINDS,
    operational_status,
)
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.request_logs.repository import RequestLogsRepository

logger = logging.getLogger(__name__)

HEALTH_CHECK_INTERVAL_SECONDS = 2 * 60 * 60
HEALTH_CHECK_RETRY_SECONDS = 60
HEALTH_CHECK_TIMEOUT_SECONDS = 30.0


@dataclass(slots=True)
class CompanyModelHealthScheduler:
    check_interval_seconds: float = HEALTH_CHECK_INTERVAL_SECONDS
    retry_seconds: float = HEALTH_CHECK_RETRY_SECONDS
    probe_timeout_seconds: float = HEALTH_CHECK_TIMEOUT_SECONDS
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._probe_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.retry_seconds)
            except TimeoutError:
                continue

    async def _probe_once(self) -> None:
        await _get_leader_election().run_if_leader(self._probe_once_as_leader)

    async def _probe_once_as_leader(self) -> None:
        async with self._lock:
            try:
                due = await self._due_sources()
                async with asyncio.TaskGroup() as group:
                    for source, models in due:
                        group.create_task(self._probe_source(source, models))
            except Exception:
                logger.exception("Company model health-check pass failed")

    async def _due_sources(self) -> list[tuple[ModelSource, list[str]]]:
        from app.modules.model_sources import codebase_llm, llmbox, trae

        async with get_background_session() as session:
            sources = list(
                (
                    await session.execute(
                        select(ModelSource)
                        .where(ModelSource.is_enabled.is_(True), ModelSource.kind.in_(COMPANY_KINDS))
                        .order_by(ModelSource.name, ModelSource.id)
                    )
                ).scalars()
            )
            bindings = {"trae": trae, "llmbox": llmbox, "codebase_llm": codebase_llm}
            sources = [
                source
                for source in sources
                if bindings[source.kind].cache_state() == "present"
                and not (await operational_status(session, source)).budget_exhausted
            ]
            models = list(
                (
                    await session.execute(
                        select(ModelSourceModel)
                        .where(
                            ModelSourceModel.source_id.in_([source.id for source in sources]),
                            ModelSourceModel.is_enabled.is_(True),
                        )
                        .order_by(ModelSourceModel.source_id, ModelSourceModel.model)
                    )
                ).scalars()
            )
            latest = {
                (row.source_id, row.model): row.checked_at
                for row in (
                    await session.execute(
                        select(
                            RequestLog.model_source_id.label("source_id"),
                            RequestLog.model,
                            func.max(RequestLog.requested_at).label("checked_at"),
                        )
                        .where(
                            RequestLog.model_source_id.in_([source.id for source in sources]),
                            RequestLog.source == COMPANY_HEALTH_CHECK_SOURCE,
                        )
                        .group_by(RequestLog.model_source_id, RequestLog.model)
                    )
                )
            }
            detach_session_objects(session)

        cutoff = utcnow().timestamp() - self.check_interval_seconds
        by_source: dict[str, list[str]] = {}
        for model in models:
            checked_at = latest.get((model.source_id, model.model))
            if checked_at is None or checked_at.timestamp() <= cutoff:
                by_source.setdefault(model.source_id, []).append(model.model)
        return [(source, by_source[source.id]) for source in sources if source.id in by_source]

    async def _probe_source(self, source: ModelSource, models: list[str]) -> None:
        bulkhead = get_source_bulkhead()
        for model in models:
            slot = bulkhead.try_acquire(source.id, source.max_concurrency)
            if slot is None:
                return
            try:
                await self._probe_model(source, model)
            finally:
                bulkhead.release(slot)

    async def _probe_model(self, source: ModelSource, model: str) -> None:
        started = time.monotonic()
        status = "error"
        error_code: str | None = None
        error_message: str | None = None
        upstream_status_code: int | None = None
        usage = None
        first_token_ms: int | None = None
        try:
            async with asyncio.timeout(self.probe_timeout_seconds):
                completion = await forward_responses(
                    source,
                    {
                        "model": model,
                        "input": "Reply with exactly OK.",
                        "stream": False,
                        "max_output_tokens": 8,
                    },
                )
            status = "success"
            usage = completion.usage
            upstream_status_code = completion.upstream_status_code
            if completion.timings is not None:
                first_token_ms = completion.timings.latency_first_token_ms
        except TimeoutError:
            error_code = "company_health_check_timeout"
            error_message = f"Health check exceeded {self.probe_timeout_seconds:g} seconds"
        except ModelSourceForwardingError as exc:
            upstream_status_code = exc.upstream_status_code
            error = exc.payload.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
                error_code = code if isinstance(code, str) else "company_health_check_failed"
                error_message = message if isinstance(message, str) else str(error)
            else:
                error_code = "company_health_check_failed"
                error_message = str(exc)
        except Exception as exc:  # noqa: BLE001 - one broken provider must not stop the scheduler
            error_code = "company_health_check_failed"
            error_message = type(exc).__name__

        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        input_tokens = usage.input_tokens if usage is not None else None
        output_tokens = usage.output_tokens if usage is not None else None
        cached_input_tokens = usage.cached_input_tokens if usage is not None else None
        async with get_background_session() as session:
            await RequestLogsRepository(session).add_log(
                account_id=None,
                request_id=f"health_{uuid4().hex}",
                model=model,
                model_source_id=source.id,
                model_source_kind=source.kind,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                latency_ms=latency_ms,
                latency_first_token_ms=first_token_ms,
                status=status,
                error_code=error_code,
                error_message=error_message,
                upstream_status_code=upstream_status_code,
                transport="http",
                upstream_transport="openai_compatible_http",
                source=COMPANY_HEALTH_CHECK_SOURCE,
                cost_usd=(
                    source_model_cost_usd(
                        source,
                        model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_input_tokens or 0,
                    )
                    if input_tokens is not None and output_tokens is not None
                    else None
                ),
            )


def build_company_model_health_scheduler() -> CompanyModelHealthScheduler:
    return CompanyModelHealthScheduler()
