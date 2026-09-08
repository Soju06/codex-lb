"""Single-owner lifecycle for a dispatched model-source attempt (#2123 WP-C1).

Interfaces contract only (design v3 §6). Every request forwarded to an
OpenAI-compatible model source is owned by exactly one ``SourceDispatch`` that
ends in exactly one ``finish()`` (I10): the source connection is closed, the
API-key reservation is settled or released exactly once (I3), the admission
claims are released, and one request-log row is written per attempt -- each
step independently latched and failure-isolated. ``abandon()`` is the
cancellation-class ``finish()`` for a client that left during the open or
before the body started (design §6.4 table).

Composition rules the implementation must keep:

* the settlement generator is the *outermost* body layer for every composition
  (it sees the terminal outcome and the cancellation first);
* ``SourceStreamingResponse.__call__`` wraps the transport in
  ``try/finally: _await_cleanup_deferring_cancellation(owner.finalize_transport(), scheduler=owner.scheduler)``;
* ``except BaseException -> abandon(); raise`` in the handler segment
  (``CancelledError`` is a ``BaseException`` and is never caught by
  ``except Exception``);
* every timer, wait and task spawn goes through ``owner.scheduler``/``owner.clock``
  (``scripts/check_proxy_timing_seams.py`` has a zero allowance for this module).

Nothing on the request path constructs an owner yet; the source-dispatch
package rewires ``_source_responses_response`` onto it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler
from app.core.types import JsonValue
from app.db.models import ModelSource
from app.modules.api_keys.service import ApiKeyData, ApiKeyRequestUsageBudget, ApiKeyUsageReservationData
from app.modules.model_sources.forwarding import (
    SourceResponsesStream,
    SourceTimings,
    SourceUsage,
    SourceUsageHolder,
)
from app.modules.proxy.model_source_pins import PinIntent, PinWriteExecutor
from app.modules.proxy.source_admission import SourceAdmission, TrialResult

T = TypeVar("T")

DispatchStatus = Literal["success", "error", "cancelled"]

# Attribution defaults for direct source routing; the overflow decision (WP-C2)
# passes its own values so this module never spells them.
DEFAULT_REQUEST_LOG_SOURCE = "model_source"
DEFAULT_DISPATCH_KIND = "direct"

# A client that leaves while the open is still pending is a stall abandonment
# once the source has been silent this long without a first frame.
STALL_EVIDENCE_SECONDS = 10.0
# Disconnect poll cadence while the source open is pending.
OPEN_DISCONNECT_POLL_SECONDS = 0.25


class CleanupScheduler(Protocol):
    """``ProxyService`` satisfies this: release retries ride its cancel-safe cleanup tasks."""

    def _schedule_cancel_safe_cleanup(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        action: str,
        request_id: str,
    ) -> asyncio.Task[None]: ...


class SourcePinCommitError(Exception):
    """The pre-content pin write did not verify as durable; the stream must not deliver content."""

    def __init__(self, outcome: Literal["not_written", "unknown"]) -> None:
        super().__init__(outcome)
        self.outcome: Literal["not_written", "unknown"] = outcome


class ClientDisconnectedDuringOpen(Exception):
    """The client left while the source open was pending."""

    def __init__(self, *, pending_seconds: float, stall: bool) -> None:
        super().__init__(f"client disconnected after {pending_seconds:.3f}s (stall={stall})")
        self.pending_seconds = pending_seconds
        self.stall = stall


@dataclass(slots=True)
class SourceDispatch:
    """Owner of one dispatched attempt; ``finish()``/``abandon()`` is the single latch."""

    request: Request
    source: ModelSource
    model: str
    api_key: ApiKeyData | None
    reservation: ApiKeyUsageReservationData | None
    claims: SourceAdmission
    admission_budget: ApiKeyRequestUsageBudget | None
    requested_service_tier: str | None
    request_log_source: str = DEFAULT_REQUEST_LOG_SOURCE
    dispatch_kind: str = DEFAULT_DISPATCH_KIND
    pin_intent: PinIntent | None = None
    pin_executor: PinWriteExecutor | None = None
    drain_until: datetime | None = None
    cleanup_scheduler: CleanupScheduler | None = None
    scheduler: Scheduler = REAL_SCHEDULER
    clock: Clock = REAL_CLOCK
    stream: SourceResponsesStream | None = None
    sent_at: float = 0.0
    first_frame_at: float | None = None
    first_output_item_seen: bool = False
    delta_chars: int = 0
    body_started: bool = False
    finished: bool = False

    async def on_first_content(self, holder: SourceUsageHolder) -> None:
        """Pin hook awaited before the first content frame; raises ``SourcePinCommitError``."""

        raise NotImplementedError

    async def close_source(self) -> None:
        raise NotImplementedError

    async def settle_or_release(self, status: DispatchStatus, usage: SourceUsage | None) -> None:
        raise NotImplementedError

    def release_claims(self, trial_result: TrialResult) -> None:
        raise NotImplementedError

    async def write_row(
        self,
        *,
        status: DispatchStatus,
        error_code: str | None,
        error_message: str | None,
        usage: SourceUsage | None,
        timings: SourceTimings | None,
        upstream_status_code: int | None,
    ) -> None:
        raise NotImplementedError

    async def finish(
        self,
        *,
        status: DispatchStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: SourceUsage | None = None,
        upstream_status_code: int | None = None,
        trial_result: TrialResult = "inconclusive",
    ) -> None:
        """``close_source -> settle_or_release -> release_claims + record_result -> write_row``; idempotent."""

        raise NotImplementedError

    async def abandon(self, reason: str) -> None:
        """Cancellation-class ``finish()`` for a client that left during the open / before the body."""

        raise NotImplementedError

    async def finalize_transport(self) -> None:
        """``aclose()`` the body, then ``finish("cancelled", "client_disconnected_before_body")`` if unfinished."""

        raise NotImplementedError


class SourceStreamingResponse(StreamingResponse):
    """``StreamingResponse`` whose transport exit always reaches ``owner.finalize_transport()``."""

    def __init__(
        self,
        content: AsyncIterator[str],
        *,
        owner: SourceDispatch,
        media_type: str = "text/event-stream",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self.owner = owner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError


async def open_with_disconnect_watch(request: Request, owner: SourceDispatch, coro: Coroutine[Any, Any, T]) -> T:
    """Run the source open while polling ``request.is_disconnected()``; raises ``ClientDisconnectedDuringOpen``."""

    raise NotImplementedError


def settlement_stream(owner: SourceDispatch, wrapped: AsyncIterator[str]) -> AsyncIterator[str]:
    """Outermost body layer: settles/releases on the terminal outcome and calls ``owner.finish()`` in ``finally``.

    Implemented as an async generator over ``wrapped``.
    """

    raise NotImplementedError


def estimate_settlement_usage(*, admission_budget: ApiKeyRequestUsageBudget | None, delta_chars: int) -> SourceUsage:
    """Settle-at-estimate figures: input = admission estimate or default; output = max(default, delta_chars // 4)."""

    raise NotImplementedError


def synthesized_pin_failure_frames(created_envelope: Mapping[str, JsonValue] | None, *, error_code: str) -> list[str]:
    """The ``response.created`` + ``response.failed`` pair (sequence 0/1) emitted on a verified pin non-write."""

    raise NotImplementedError
