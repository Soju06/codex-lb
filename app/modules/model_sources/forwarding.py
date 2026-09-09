from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractContextManager, AsyncExitStack, nullcontext
from dataclasses import dataclass, field
from json import JSONDecodeError
from math import isfinite
from typing import Any, Literal, cast

import aiohttp

from app.core.clients.http import lease_model_source_session
from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler
from app.core.config.dashboard_overrides import with_dashboard_overrides
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.parsing import classify_event_type
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.shared_future import (
    _await_cleanup_deferring_cancellation as _shared_await_cleanup_deferring_cancellation,
)
from app.core.utils.shared_future import _await_task_deferring_cancellation
from app.core.utils.sse import extract_sse_data
from app.db.models import ModelSource

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE_TIMEOUT_SECONDS = 600

# Bounded exposure for OpenAI-compatible model-source transport (#2123 WP-C1,
# design v3 §3, §8.2). Single definition: callers import, never re-literal.
SOURCE_CONNECT_DEADLINE_SECONDS = 10.0
# Responses streams only (``stream_responses``): a compliant source emits
# ``response.created`` within milliseconds of its headers, so headers-then-
# queue is exactly what these two catch. Chat-completions streams have no
# bookkeeping frame -- the first byte is the first token, and OpenAI-compatible
# local servers (llama.cpp, vLLM, Ollama) send it, often together with their
# headers, only after prompt processing -- so their open returns at the headers
# as before the hardening and the body reads the first token under the
# source's total budget alone.
SOURCE_HEADER_DEADLINE_SECONDS = 20.0
# Time from response headers to the first body chunk.
SOURCE_FIRST_FRAME_DEADLINE_SECONDS = 30.0
# Mid-stream silence cap; ``source_stream_idle_seconds()`` takes the minimum
# with ``settings.stream_idle_timeout_seconds`` so a source never inherits the
# 7200 s subscription idle window.
SOURCE_STREAM_IDLE_CAP_SECONDS = 300.0
# Aggregate bytes a stream may withhold while a pre-content hook is armed
# (``_source_stream_body``): twice the parser's single-frame cap, so one
# oversized frame always reaches the parser's content classification before
# this bound trips, while a source that only ever produces bookkeeping frames
# cannot grow the withheld buffer for its whole total budget.
SOURCE_STREAM_WITHHELD_CAP_BYTES = 2 * 1_048_576

TimeoutPhase = Literal["connect", "header", "first_frame", "idle"]
FrameKind = Literal["non_content", "content", "success_terminal", "failure_terminal"]


class ModelSourceForwardingError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        payload: dict[str, JsonValue],
        upstream_status_code: int | None = None,
        retry_after: str | None = None,
        timeout_phase: TimeoutPhase | None = None,
    ) -> None:
        super().__init__(str(payload))
        self.status_code = status_code
        self.payload = payload
        self.upstream_status_code = upstream_status_code
        # Source ``Retry-After`` for honest passthrough of 429/5xx (I8).
        self.retry_after = retry_after
        # Which bounded phase expired for ``model_source_timeout``/``model_source_idle_timeout``.
        self.timeout_phase = timeout_phase


@dataclass(frozen=True, slots=True)
class SourceUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class SourceTimings:
    """Server-reported generation timing, for TTFT/tokens-per-second reporting.

    Maps directly onto ``RequestLog.latency_first_token_ms`` /
    ``RequestLog.latency_ms`` so source-routed requests get the same TTFT and
    tokens-per-second dashboard reporting as subscription-backed ones, sourced
    from the upstream's own measurements rather than proxy-side timers (the
    proxy does not instrument source forwarding round trips itself).
    """

    latency_first_token_ms: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class SourceChatCompletion:
    payload: dict[str, JsonValue]
    usage: SourceUsage | None
    timings: SourceTimings | None
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class SourceResponsesCompletion:
    payload: dict[str, JsonValue]
    usage: SourceUsage | None
    timings: SourceTimings | None
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class SourceAudioTranscription:
    body: bytes
    content_type: str | None
    usage: SourceUsage | None
    audio_seconds: float | None
    timings: SourceTimings | None
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class SourceEmbeddings:
    payload: dict[str, JsonValue]
    usage: SourceUsage | None
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class _TransportOwnedStream:
    """A source stream whose ``body`` and direct ``aclose()`` converge on one transport release.

    ``transport`` owns the session lease and the upstream response (``None``
    for the synthetic streams tests build) and is shared with ``body`` so both
    close paths meet at its single latch. ``aclose()`` on a never-started
    async generator skips its ``finally`` block, so a stream that is torn down
    before iteration begins -- a client that leaves between the route
    returning and Starlette's first body write -- would otherwise keep the
    pooled lease and the upstream connection until garbage collection.
    """

    transport: "SourceStreamTransport | None" = field(default=None, kw_only=True, repr=False, compare=False)

    async def aclose(self) -> None:
        """Release the source connection directly (idempotent), even if ``body`` was never started."""

        if self.transport is not None:
            await self.transport.aclose()


@dataclass(frozen=True, slots=True)
class SourceChatStream(_TransportOwnedStream):
    body: AsyncIterator[bytes]
    usage_holder: "SourceUsageHolder"
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class SourceResponsesStream(_TransportOwnedStream):
    body: AsyncIterator[bytes]
    usage_holder: "SourceUsageHolder"
    upstream_status_code: int


@dataclass(slots=True)
class SourceUsageHolder:
    usage: SourceUsage | None = None
    timings: SourceTimings | None = None
    # Responses-stream observations for the dispatch owner (#2123 WP-C1, design §6).
    response_id: str | None = None
    created_envelope: dict[str, JsonValue] | None = None
    first_frame_at: float | None = None
    # A frame that delivers response content to the client -- a ``content``
    # frame or a success terminal -- has been parsed (I11: delivered => pinned).
    first_content_seen: bool = False
    first_output_item_seen: bool = False
    # Set at the flush point of ``_source_stream_body``: a chunk carrying
    # content (``first_content_seen``) has been handed to the consumer, after
    # the pre-content hook and never before it. This is the body's evidence for
    # the hook ordering (I11); it is not the billing signal. The consumer is the
    # public wrapper chain, which may still drop, park or hold the frame, so
    # ``SourceDispatch.content_delivered`` is derived by the settlement layer
    # from the frames it hands to the transport and never mirrored from here.
    content_delivered: bool = False
    terminal_kind: Literal["completed", "incomplete", "failed", "error"] | None = None
    delta_chars: int = 0


# Awaited once, before the first content frame is released to the client
# (the pin hook); a failure terminal with no prior content never invokes it.
OnFirstContent = Callable[[SourceUsageHolder], Awaitable[None]]

_NON_CONTENT_EVENT_TYPES = frozenset({"response.created", "response.in_progress", "response.queued"})
_SUCCESS_TERMINAL_EVENT_TYPES = frozenset({"response.completed", "response.incomplete"})
_FAILURE_TERMINAL_EVENT_TYPES = frozenset({"response.failed", "error"})
_FAILURE_TERMINAL_KINDS = frozenset({"failed", "error"})
_SOURCE_STREAM_CHUNK_BYTES = 4096


def source_stream_idle_seconds() -> float:
    """``min(settings.stream_idle_timeout_seconds, SOURCE_STREAM_IDLE_CAP_SECONDS)``.

    The subscription idle window (7200 s by default) is sized for ChatGPT
    backends the proxy trusts; an operator-configured source gets at most the
    cap so a silent source never holds a client stream open for hours.
    """

    return min(
        float(with_dashboard_overrides(get_settings()).stream_idle_timeout_seconds), SOURCE_STREAM_IDLE_CAP_SECONDS
    )


def classify_responses_frame(event_type: str | None) -> FrameKind:
    """Classify a Responses SSE frame by its event ``type`` for the pre-content hook.

    ``response.created`` / ``response.in_progress`` / ``response.queued`` are
    bookkeeping the client does not need before content and are withheld while
    a hook is armed; ``None`` (a frame without a typed event: SSE comments and
    keepalives, the ``[DONE]`` sentinel, unparseable ``data``) is treated the
    same way. ``response.completed`` / ``response.incomplete`` are success
    terminals, ``response.failed`` / ``error`` failure terminals. Every other
    type -- ``response.output_item.added``, the ``*.delta`` family, and any
    event this proxy does not know -- is ``content``: unknown frames are never
    withheld past the hook, so a newer event vocabulary cannot stall a stream.
    Callers read the type with the public wrapper's own ``classify_event_type``
    so a typeless ``{"error": {...}}`` record is the ``error`` terminal on both
    sides of the pipeline.
    """

    if event_type is None or event_type in _NON_CONTENT_EVENT_TYPES:
        return "non_content"
    if event_type in _SUCCESS_TERMINAL_EVENT_TYPES:
        return "success_terminal"
    if event_type in _FAILURE_TERMINAL_EVENT_TYPES:
        return "failure_terminal"
    return "content"


async def _await_cleanup_deferring_cancellation(
    awaitable: Awaitable[object],
    *,
    scheduler: Scheduler = REAL_SCHEDULER,
) -> None:
    """Finish owned upstream cleanup even if the caller is cancelled again."""

    await _shared_await_cleanup_deferring_cancellation(awaitable, scheduler=scheduler)


async def _await_result_deferring_cancellation(
    awaitable: Awaitable[object],
    *,
    scheduler: Scheduler = REAL_SCHEDULER,
) -> bool:
    """Finish owned cleanup and report whether cancellation arrived mid-flight."""

    return await _shared_await_cleanup_deferring_cancellation(awaitable, scheduler=scheduler) is not None


class SourceStreamTransport:
    """Single close latch for the exit stack that owns a source stream (session lease + response).

    ``AsyncExitStack.aclose`` runs each callback exactly once, but two callers
    closing concurrently (the body's ``finally`` and a direct
    ``SourceResponsesStream.aclose``) would split the callbacks between them
    and release the lease before the response. The close therefore runs in one
    owned task that every caller awaits deferring its own cancellation, so a
    Starlette scope that keeps re-cancelling the response task still returns
    the pooled lease.
    """

    __slots__ = ("_close_task", "_scheduler", "_stack")

    def __init__(self, stack: AsyncExitStack, *, scheduler: Scheduler) -> None:
        self._stack = stack
        self._scheduler = scheduler
        self._close_task: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = self._scheduler.create_task(self._stack.aclose())
        await _await_task_deferring_cancellation(self._close_task)


async def forward_chat_completion(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceChatCompletion:
    stack = AsyncExitStack()
    try:
        session = await stack.enter_async_context(lease_model_source_session())
        response = await stack.enter_async_context(
            session.post(
                _source_url(source, "/chat/completions"),
                headers=_source_headers(source, encryptor=encryptor),
                json=payload,
                timeout=_source_client_timeout(source),
            )
        )
        data = await _response_json(response)
        if response.status >= 400:
            raise _upstream_status_error(response, source, encryptor=encryptor, error_payload=_error_payload(data))
        if data is None:
            raise _invalid_upstream_response_error(response.status)
        result = SourceChatCompletion(
            payload=data,
            usage=_usage_from_chat_payload(data),
            timings=_timings_from_payload(data),
            upstream_status_code=response.status,
        )
    except (aiohttp.ClientError, TimeoutError) as exc:
        await _await_cleanup_deferring_cancellation(stack.aclose())
        raise _unreachable_error(exc) from exc
    except BaseException:
        await _await_cleanup_deferring_cancellation(stack.aclose())
        raise

    cleanup_cancelled = await _await_result_deferring_cancellation(stack.aclose())
    if cleanup_cancelled:
        raise asyncio.CancelledError
    return result


async def stream_chat_completion(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
    scheduler: Scheduler = REAL_SCHEDULER,
    clock: Clock = REAL_CLOCK,
) -> SourceChatStream:
    usage_holder = SourceUsageHolder()
    usage_parser = SourceStreamUsageParser(usage_holder, response_shape="chat")
    # Chat completions keep the source's own 401/403 envelope (recode is a
    # Responses-dispatch decision) and return at the source's headers exactly
    # as before the hardening: the first byte is the first token, which a local
    # source produces only after prompt processing, so the body reads it under
    # the source's total budget alone while the client already holds the
    # ``200`` -- a client that leaves during prompt processing cancels the body
    # and releases the connection, the pooled lease and the reservation at
    # once instead of holding all of them until the first token. The connect
    # bound, the dedicated connector and the mid-stream idle cap apply.
    stack, response, first_chunk = await _open_source_stream(
        source,
        "/chat/completions",
        payload,
        encryptor=encryptor,
        scheduler=scheduler,
        clock=clock,
        header_deadline_seconds=None,
        first_frame_deadline_seconds=None,
    )
    transport = SourceStreamTransport(stack, scheduler=scheduler)
    body = _source_stream_body(
        response,
        first_chunk,
        usage_parser,
        usage_holder,
        transport,
        on_first_content=None,
        idle_seconds=source_stream_idle_seconds(),
        scheduler=scheduler,
        clock=clock,
    )
    return SourceChatStream(
        body=body,
        usage_holder=usage_holder,
        upstream_status_code=response.status,
        transport=transport,
    )


async def forward_responses(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
    recode_credential_failures: bool = True,
) -> SourceResponsesCompletion:
    try:
        async with lease_model_source_session() as session:
            # Non-stream generations legitimately spend minutes before the
            # first byte, so only connect establishment and the source's total
            # budget are bounded here (no header/first-frame deadline).
            async with session.post(
                _source_url(source, "/responses"),
                headers=_source_headers(source, encryptor=encryptor),
                json=payload,
                timeout=_source_client_timeout(source),
            ) as response:
                if response.status >= 400:
                    if recode_credential_failures and response.status in _CREDENTIAL_REJECTION_STATUSES:
                        raise _credentials_rejected_error(response, source)
                    data = await _response_json(response)
                    raise _upstream_status_error(
                        response, source, encryptor=encryptor, error_payload=_error_payload(data)
                    )
                data = await _response_json(response)
                if data is None:
                    raise _invalid_upstream_response_error(response.status)
                return SourceResponsesCompletion(
                    payload=data,
                    usage=_usage_from_responses_payload(data),
                    timings=_timings_from_payload(data),
                    upstream_status_code=response.status,
                )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _unreachable_error(exc) from exc


async def forward_audio_transcription(
    source: ModelSource,
    *,
    audio_bytes: bytes,
    filename: str,
    content_type: str | None,
    fields: list[tuple[str, str]],
    encryptor: TokenEncryptor | None = None,
) -> SourceAudioTranscription:
    normalized_filename = filename.strip() if filename else "audio.wav"
    normalized_content_type = content_type.strip() if content_type else "application/octet-stream"
    form = aiohttp.FormData()
    form.add_field(
        "file",
        audio_bytes,
        filename=normalized_filename,
        content_type=normalized_content_type,
    )
    for key, value in fields:
        form.add_field(key, value)
    try:
        async with lease_model_source_session() as session:
            async with session.post(
                _source_url(source, "/audio/transcriptions"),
                headers=_source_headers(source, encryptor=encryptor, accept="*/*", content_type=None),
                data=form,
                timeout=_source_client_timeout(source),
            ) as response:
                body = await response.read()
                response_content_type = response.headers.get("Content-Type")
                if response.status >= 400:
                    raise _upstream_status_error(
                        response,
                        source,
                        encryptor=encryptor,
                        error_payload=_error_payload_from_body(body, response_content_type),
                    )
                return SourceAudioTranscription(
                    body=body,
                    content_type=response_content_type,
                    usage=_usage_from_audio_body(body, response_content_type),
                    audio_seconds=_audio_seconds_from_body(body, response_content_type),
                    timings=_timings_from_audio_body(body, response_content_type),
                    upstream_status_code=response.status,
                )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _unreachable_error(exc) from exc


async def forward_embeddings(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceEmbeddings:
    try:
        async with lease_model_source_session() as session:
            async with session.post(
                _source_url(source, "/embeddings"),
                headers=_source_headers(source, encryptor=encryptor),
                json=payload,
                timeout=_source_client_timeout(source),
            ) as response:
                data = await _response_json(response)
                if response.status >= 400:
                    raise _upstream_status_error(
                        response, source, encryptor=encryptor, error_payload=_error_payload(data)
                    )
                if data is None:
                    raise _invalid_upstream_response_error(response.status)
                return SourceEmbeddings(
                    payload=data,
                    usage=_usage_from_embeddings_payload(data),
                    upstream_status_code=response.status,
                )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _unreachable_error(exc) from exc


async def stream_responses(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
    on_first_content: OnFirstContent | None = None,
    recode_credential_failures: bool = True,
    scheduler: Scheduler = REAL_SCHEDULER,
    clock: Clock = REAL_CLOCK,
) -> SourceResponsesStream:
    usage_holder = SourceUsageHolder()
    usage_parser = SourceStreamUsageParser(usage_holder, response_shape="responses")
    stack, response, first_chunk = await _open_source_stream(
        source,
        "/responses",
        payload,
        encryptor=encryptor,
        recode_credential_failures=recode_credential_failures,
        scheduler=scheduler,
        clock=clock,
    )
    # The open returns with the first frame in hand, so this is the instant the
    # source proved it is producing; the body yields that frame first.
    usage_holder.first_frame_at = clock.monotonic()
    transport = SourceStreamTransport(stack, scheduler=scheduler)
    body = _source_stream_body(
        response,
        first_chunk,
        usage_parser,
        usage_holder,
        transport,
        on_first_content=on_first_content,
        idle_seconds=source_stream_idle_seconds(),
        scheduler=scheduler,
        clock=clock,
    )
    return SourceResponsesStream(
        body=body,
        usage_holder=usage_holder,
        upstream_status_code=response.status,
        transport=transport,
    )


class _SourceBudgetExpired(Exception):
    """aiohttp's own total-budget timer fired inside a chunk read (not the idle cap)."""

    def __init__(self, original: TimeoutError) -> None:
        super().__init__(str(original))
        self.original = original


async def _next_source_chunk(chunks: AsyncIterator[bytes]) -> bytes | None:
    """One chunk, ``None`` at EOF; aiohttp's total-budget ``TimeoutError`` is tagged so the idle timer is not blamed."""

    try:
        return await anext(chunks)
    except StopAsyncIteration:
        return None
    except TimeoutError as exc:
        raise _SourceBudgetExpired(exc) from exc


async def _first_source_chunk(chunks: AsyncIterator[bytes]) -> bytes | None:
    """The first chunk of a stream whose open returned at the headers, read under the source's total budget alone.

    A budget or transport failure is the ``502 model_source_unreachable``
    verdict the open gives the first-frame phase; ``None`` when the ``2xx``
    stream ends before its first chunk -- the client already holds the
    ``200`` and headers, so the caller ends the body instead of raising into
    it (decision 50). No idle timer: the pre-first-token silence of a
    chat-completions source is prompt processing.
    """

    try:
        return await anext(chunks)
    except StopAsyncIteration:
        return None
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _unreachable_error(exc) from exc


async def _source_stream_body(
    response: aiohttp.ClientResponse,
    first_chunk: bytes | None,
    usage_parser: SourceStreamUsageParser,
    usage_holder: SourceUsageHolder,
    transport: SourceStreamTransport,
    *,
    on_first_content: OnFirstContent | None,
    idle_seconds: float,
    scheduler: Scheduler,
    clock: Clock,
) -> AsyncIterator[bytes]:
    """Relay source bytes chunk by chunk; the transport is released exactly once.

    ``first_chunk`` is the chunk the open already read (Responses streams,
    yielded before reading further) or ``None`` when the open returned at the
    headers (chat-completions streams): then the first chunk is read here,
    under the source's total budget alone and without the idle timer, and
    ``usage_holder.first_frame_at`` is stamped when it arrives. A ``2xx``
    source that closes before that first chunk ends the body cleanly -- the
    client already holds the ``200`` and headers, so the empty stream ``main``
    relayed is what it receives, never an exception out of a started body --
    and ``first_frame_at`` stays ``None`` as the stream owner's evidence for
    the ``invalid_upstream_response`` verdict (decision 50). Every chunk
    read after the first frame is bounded by ``idle_seconds``
    (``source_stream_idle_seconds()``) through the scheduler seam rather than
    aiohttp's ``sock_read``: the socket timer is armed from request send and a
    low configured idle window would pre-empt the header and first-frame
    deadlines, whereas this timer starts only once the source has proved it is
    producing. Expiry raises ``model_source_idle_timeout``; the source's own
    total budget (aiohttp ``total``) keeps propagating as today.

    Without a hook every chunk is yielded as soon as it is parsed (the direct
    routing path adds no buffering). With ``on_first_content`` armed, chunks
    are withheld until the parser sees the first frame that delivers content
    (a ``content`` frame or a success terminal); the hook is awaited once and
    the withheld bytes are flushed in order behind it. A failure terminal with
    no prior content flushes without the hook. At EOF the parser's unterminated
    tail is parsed as a final frame first (a record the source closed with a
    single newline is still a frame the client will parse), and only a tail
    that delivers nothing flushes without the hook: nothing was delivered, so
    there is nothing to pin. A frame that outgrows the parser's buffer cap
    counts as content (it cannot be a bookkeeping envelope and its truncated
    remainder never parses), so an oversized terminal never reaches the client
    unpinned. The withheld bytes are bounded by
    ``SOURCE_STREAM_WITHHELD_CAP_BYTES``: a source that keeps producing
    complete bookkeeping frames past that bound fails closed with
    ``502 invalid_upstream_response`` instead of growing the buffer for its
    whole total budget. A hook exception propagates and the withheld bytes are
    dropped -- the client must not receive content whose continuity was not
    secured. ``usage_holder.content_delivered`` is set exactly where a chunk
    carrying content is handed to the consumer (after the hook, never before)
    as the body's hook-ordering evidence; the consumer is the public wrapper
    chain, which can still drop or park that chunk, so the billing decision
    (``SourceDispatch.content_delivered``) is made by the settlement layer from
    the frames it hands to the transport, not from this flag.
    """

    withheld: list[bytes] | None = [] if on_first_content is not None else None
    withheld_bytes = 0
    chunks = response.content.iter_chunked(_SOURCE_STREAM_CHUNK_BYTES)
    chunk: bytes | None = first_chunk
    try:
        if chunk is None:
            # The open returned at the headers: a client that leaves during
            # prompt processing cancels this wait, and the ``finally`` below
            # releases the connection and the pooled lease at once.
            chunk = await _first_source_chunk(chunks)
            if chunk is None:
                # EOF before the first chunk: a clean empty stream to the
                # client that already holds the ``200`` (``main`` parity).
                return
            usage_holder.first_frame_at = clock.monotonic()
        while True:
            if chunk is None:
                try:
                    chunk = await scheduler.wait_for(_next_source_chunk(chunks), idle_seconds)
                except _SourceBudgetExpired as exc:
                    raise exc.original from exc.original.__cause__
                except TimeoutError as exc:
                    raise _idle_timeout_error(idle_seconds) from exc
                if chunk is None:
                    # EOF: an unterminated final record is still a frame.
                    usage_parser.finish()
                    break
            usage_parser.feed(chunk)
            if withheld is None:
                if usage_holder.first_content_seen:
                    usage_holder.content_delivered = True
                yield chunk
            else:
                withheld.append(chunk)
                withheld_bytes += len(chunk)
                if usage_holder.first_content_seen:
                    assert on_first_content is not None
                    await on_first_content(usage_holder)
                    usage_holder.content_delivered = True
                    released, withheld = withheld, None
                    for pending in released:
                        yield pending
                elif usage_holder.terminal_kind in _FAILURE_TERMINAL_KINDS:
                    released, withheld = withheld, None
                    for pending in released:
                        yield pending
                elif withheld_bytes > SOURCE_STREAM_WITHHELD_CAP_BYTES:
                    raise _withheld_cap_error(withheld_bytes)
            chunk = None
        if withheld:
            if usage_holder.first_content_seen:
                # The unterminated tail delivered content (I11: delivered => pinned).
                assert on_first_content is not None
                await on_first_content(usage_holder)
                usage_holder.content_delivered = True
            for pending in withheld:
                yield pending
    finally:
        # A plain ``async with stack`` unwinds unshielded: repeated
        # cancellation delivery can interrupt ``__aexit__`` mid-unwind and
        # leak the pooled HTTP session lease.
        await transport.aclose()


async def _open_source_stream(
    source: ModelSource,
    path: str,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None,
    recode_credential_failures: bool = False,
    scheduler: Scheduler = REAL_SCHEDULER,
    clock: Clock = REAL_CLOCK,
    header_deadline_seconds: float | None = SOURCE_HEADER_DEADLINE_SECONDS,
    first_frame_deadline_seconds: float | None = SOURCE_FIRST_FRAME_DEADLINE_SECONDS,
) -> tuple[AsyncExitStack, aiohttp.ClientResponse, bytes | None]:
    """Open the upstream request eagerly so errors surface before headers.

    Streaming callers wrap the returned response in a ``StreamingResponse``;
    anything raised after that point arrives after the 200 status line has
    been sent. Opening the request here lets upstream 4xx/5xx, connection
    failures and a source that accepts the request but never produces a frame
    map to a proper OpenAI error response instead of a truncated stream. The
    open is bounded per phase (design v3 §8.2): connect establishment by
    ``ClientTimeout``, the header wait by ``header_deadline_seconds`` and the
    first body chunk by ``first_frame_deadline_seconds``, both through
    ``scheduler.fail_after`` so virtual time can expire them. ``None`` leaves
    the header wait to the source's total budget; ``None`` for the first-frame
    deadline means the open does not read the first chunk at all and returns
    at the headers, as it did before the hardening (chat-completions streams,
    whose first byte is the first token): the body reads it under the total
    budget while the client already holds the ``200``, so a client that leaves
    during prompt processing frees the connection, the pooled lease and the
    reservation instead of holding them until the first token. The mid-stream
    idle cap is the body's (``_source_stream_body``). The returned exit stack
    owns the session lease and response and must be closed by the stream body
    (or the stream's ``aclose()``); the returned bytes are the first
    chunk, which the body yields before reading further, or ``None`` when the
    body reads it.
    """
    stack = AsyncExitStack()
    opened_at = clock.monotonic()
    try:
        session = await stack.enter_async_context(lease_model_source_session())
        try:
            with _phase_deadline(scheduler, header_deadline_seconds):
                response = await stack.enter_async_context(
                    session.post(
                        _source_url(source, path),
                        headers=_source_headers(source, encryptor=encryptor, stream=True),
                        json=payload,
                        timeout=_source_client_timeout(source),
                    )
                )
        except aiohttp.ConnectionTimeoutError as exc:
            # ``connect`` / ``sock_connect`` expired: the source never accepted
            # a connection, which is the existing "unreachable" verdict.
            raise _unreachable_error(exc, timeout_phase="connect") from exc
        except aiohttp.ClientError as exc:
            raise _unreachable_error(exc) from exc
        except TimeoutError as exc:
            if header_deadline_seconds is None:
                # Only the source's total budget was armed: the pre-hardening verdict.
                raise _unreachable_error(exc) from exc
            raise _timeout_error("header", source, elapsed=clock.monotonic() - opened_at) from exc
        if response.status >= 400:
            if recode_credential_failures and response.status in _CREDENTIAL_REJECTION_STATUSES:
                raise _credentials_rejected_error(response, source)
            data = await _read_error_body(response, scheduler=scheduler)
            raise _upstream_status_error(response, source, encryptor=encryptor, error_payload=_error_payload(data))
        if first_frame_deadline_seconds is None:
            return stack, response, None
        try:
            with _phase_deadline(scheduler, first_frame_deadline_seconds):
                first_chunk = await response.content.readany()
        except TimeoutError as exc:
            # anyio's deadline (or the source's total budget, a ``TimeoutError``
            # too) -- either way the source accepted the request and produced
            # nothing.
            raise _timeout_error("first_frame", source, elapsed=clock.monotonic() - opened_at) from exc
        except aiohttp.ClientError as exc:
            raise _unreachable_error(exc) from exc
        if not first_chunk:
            raise empty_stream_error(response.status)
        return stack, response, first_chunk
    except BaseException:
        await _await_cleanup_deferring_cancellation(stack.aclose(), scheduler=scheduler)
        raise


_CREDENTIAL_REJECTION_STATUSES = frozenset({401, 403})


def _phase_deadline(scheduler: Scheduler, seconds: float | None) -> AbstractContextManager[Any]:
    """``scheduler.fail_after(seconds)``, or no bound at all when the phase is left to the source's total budget."""

    return scheduler.fail_after(seconds) if seconds is not None else nullcontext()


def _source_client_timeout(source: ModelSource) -> aiohttp.ClientTimeout:
    """Per-request timeout: the source's total budget plus bounded TCP establishment.

    Only ``sock_connect`` bounds the 10 s connection-establishment deadline, not
    ``connect``: in aiohttp ``connect`` *also* bounds the wait for a free pooled
    connection, so a source whose dedicated pool is saturated at
    ``http_connector_limit_per_host`` would fail fast at 10 s with a misleading
    ``model_source_unreachable`` verdict (and feed false breaker trips) instead
    of queuing for a slot -- exactly the shape a whole exhausted pool funnelling
    to one designated source produces, where ``max_concurrency`` unset promises
    'unlimited'. ``sock_connect`` bounds only a *new* connection's TCP handshake;
    a genuine connect timeout still surfaces as ``ConnectionTimeoutError``
    (``ClientSession._request`` wraps the ``sock_connect`` ``TimeoutError``), so
    the ``connect`` phase verdict is unchanged. The pool-slot wait falls back to
    the source's total budget (and, for a streaming Responses open, the header
    deadline armed on the scheduler seam).

    ``sock_read`` stays unset on purpose: aiohttp arms it from request send, so
    it would shorten the header and first-frame phases under a low configured
    idle window, and a non-stream generation that sends nothing until its
    final JSON body must be bounded by the total budget alone. Stream idle is
    enforced per chunk by ``_source_stream_body``.
    """

    return aiohttp.ClientTimeout(
        total=_source_timeout_seconds(source),
        sock_connect=SOURCE_CONNECT_DEADLINE_SECONDS,
    )


async def _read_error_body(response: aiohttp.ClientResponse, *, scheduler: Scheduler) -> dict[str, JsonValue] | None:
    """Read an error body under the first-frame deadline; ``None`` keeps the honest status with a generic envelope."""

    try:
        with scheduler.fail_after(SOURCE_FIRST_FRAME_DEADLINE_SECONDS):
            return await _response_json(response)
    except TimeoutError:
        return None


def _retry_after_header(response: aiohttp.ClientResponse) -> str | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _upstream_status_error(
    response: aiohttp.ClientResponse,
    source: ModelSource,
    *,
    encryptor: TokenEncryptor | None,
    error_payload: dict[str, JsonValue],
) -> ModelSourceForwardingError:
    """Honest passthrough of a source 4xx/5xx: status, redacted envelope and ``Retry-After``."""

    return ModelSourceForwardingError(
        status_code=response.status,
        payload=_redact_source_error_payload(error_payload, source, encryptor=encryptor),
        upstream_status_code=response.status,
        retry_after=_retry_after_header(response),
    )


def _credentials_rejected_error(response: aiohttp.ClientResponse, source: ModelSource) -> ModelSourceForwardingError:
    """Source 401/403 as a generic 502: the source's message embeds a masked copy of the proxy's key.

    The body is never read, parsed, or logged; the server-side record is the
    status alone.
    """

    logger.warning(
        "OpenAI-compatible model source %s rejected the proxy's credentials (HTTP %s)",
        source.id,
        response.status,
    )
    return ModelSourceForwardingError(
        status_code=502,
        payload={
            "error": {
                "message": "OpenAI-compatible model source rejected the proxy's credentials",
                "type": "upstream_error",
                "code": "model_source_credentials_error",
            }
        },
        upstream_status_code=response.status,
        retry_after=_retry_after_header(response),
    )


def _timeout_error(phase: TimeoutPhase, source: ModelSource, *, elapsed: float) -> ModelSourceForwardingError:
    deadline = SOURCE_HEADER_DEADLINE_SECONDS if phase == "header" else SOURCE_FIRST_FRAME_DEADLINE_SECONDS
    logger.warning(
        "OpenAI-compatible model source %s exceeded the %s deadline (%.0fs) after %.1fs",
        source.id,
        phase,
        deadline,
        elapsed,
    )
    detail = "response headers" if phase == "header" else "the first response frame"
    return ModelSourceForwardingError(
        status_code=504,
        payload={
            "error": {
                "message": f"OpenAI-compatible model source did not send {detail} within {deadline:.0f}s",
                "type": "upstream_error",
                "code": "model_source_timeout",
            }
        },
        upstream_status_code=None,
        timeout_phase=phase,
    )


def _idle_timeout_error(idle_seconds: float) -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=504,
        payload={
            "error": {
                "message": f"OpenAI-compatible model source stream went idle for {idle_seconds:.0f}s",
                "type": "upstream_error",
                "code": "model_source_idle_timeout",
            }
        },
        upstream_status_code=None,
        timeout_phase="idle",
    )


def empty_stream_error(response_status: int) -> ModelSourceForwardingError:
    """A ``2xx`` stream that ended before its first chunk: the open's verdict, and the stream owners' row verdict."""

    return ModelSourceForwardingError(
        status_code=502,
        payload={
            "error": {
                "message": "OpenAI-compatible model source closed the stream before the first frame",
                "type": "upstream_error",
                "code": "invalid_upstream_response",
            }
        },
        upstream_status_code=response_status,
    )


def _withheld_cap_error(withheld_bytes: int) -> ModelSourceForwardingError:
    """A hook is armed and the source produced only bookkeeping past the withheld-bytes cap (fail closed, I11)."""

    return ModelSourceForwardingError(
        status_code=502,
        payload={
            "error": {
                "message": (
                    "OpenAI-compatible model source sent "
                    f"{withheld_bytes} bytes without a content frame (limit {SOURCE_STREAM_WITHHELD_CAP_BYTES})"
                ),
                "type": "upstream_error",
                "code": "invalid_upstream_response",
            }
        },
        upstream_status_code=None,
    )


def _unreachable_error(exc: Exception, *, timeout_phase: TimeoutPhase | None = None) -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=502,
        payload={
            "error": {
                "message": f"OpenAI-compatible model source request failed: {exc.__class__.__name__}",
                "type": "upstream_error",
                "code": "model_source_unreachable",
            }
        },
        upstream_status_code=None,
        timeout_phase=timeout_phase,
    )


def _source_url(source: ModelSource, path: str) -> str:
    return f"{source.base_url.rstrip('/')}{path}"


def _source_headers(
    source: ModelSource,
    *,
    encryptor: TokenEncryptor | None,
    stream: bool = False,
    accept: str | None = None,
    content_type: str | None = "application/json",
) -> dict[str, str]:
    headers = {
        "Accept": accept or ("text/event-stream" if stream else "application/json"),
    }
    if content_type is not None:
        headers["Content-Type"] = content_type
    if source.api_key_encrypted is not None:
        secret = _source_api_key_secret(source, encryptor=encryptor)
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _source_api_key_secret(source: ModelSource, *, encryptor: TokenEncryptor | None) -> str:
    if source.api_key_encrypted is None:
        return ""
    active_encryptor = encryptor or TokenEncryptor()
    try:
        return active_encryptor.decrypt(source.api_key_encrypted)
    except Exception as exc:
        # A rotated encryption key file or corrupt DB value must surface as a
        # forwarding error (with reservation release at the routes), not a bare 500.
        raise ModelSourceForwardingError(
            status_code=502,
            payload={
                "error": {
                    "message": "OpenAI-compatible model source credentials could not be decrypted",
                    "type": "upstream_error",
                    "code": "model_source_credentials_error",
                }
            },
            upstream_status_code=None,
        ) from exc


def _redact_source_error_payload(
    payload: dict[str, JsonValue],
    source: ModelSource,
    *,
    encryptor: TokenEncryptor | None,
) -> dict[str, JsonValue]:
    if source.api_key_encrypted is None:
        return payload
    secret = _source_api_key_secret(source, encryptor=encryptor)
    if not secret:
        return payload
    redacted = _redact_json_value(payload, secret)
    return cast(dict[str, JsonValue], redacted) if isinstance(redacted, Mapping) else payload


def _redact_json_value(value: JsonValue, secret: str) -> JsonValue:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, list):
        return [_redact_json_value(item, secret) for item in value]
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, JsonValue], value)
        return {key: _redact_json_value(item, secret) for key, item in mapping.items()}
    return value


def _source_timeout_seconds(source: ModelSource) -> float:
    return float(source.timeout_seconds or _DEFAULT_SOURCE_TIMEOUT_SECONDS)


async def _response_json(response: aiohttp.ClientResponse) -> dict[str, JsonValue] | None:
    """Parsed JSON object body, or ``None`` when the body is not valid JSON."""
    try:
        data = await response.json(content_type=None)
    except Exception:
        return None
    return data if isinstance(data, dict) else {"data": data}


def _invalid_upstream_response_error(response_status: int) -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=502,
        payload={
            "error": {
                "message": "OpenAI-compatible model source returned a non-JSON response",
                "type": "upstream_error",
                "code": "invalid_upstream_response",
            }
        },
        upstream_status_code=response_status,
    )


def _error_payload(data: Mapping[str, JsonValue] | None) -> dict[str, JsonValue]:
    error = data.get("error") if data is not None else None
    if is_json_mapping(error):
        return {"error": dict(error)}
    return {
        "error": {
            "message": "OpenAI-compatible model source returned an error",
            "type": "upstream_error",
            "code": "model_source_error",
        }
    }


def _error_payload_from_body(body: bytes, content_type: str | None) -> dict[str, JsonValue]:
    if _is_json_content_type(content_type):
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, JSONDecodeError):
            parsed = None
        if isinstance(parsed, Mapping):
            return _error_payload(parsed)
    upstream_message = _text_error_message(body)
    return {
        "error": {
            "message": upstream_message or "OpenAI-compatible model source returned an error",
            "type": "upstream_error",
            "code": "model_source_error",
        }
    }


def _text_error_message(body: bytes) -> str | None:
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    return text[:1000]


def _usage_from_chat_payload(payload: Mapping[str, JsonValue]) -> SourceUsage | None:
    usage = payload.get("usage")
    if not is_json_mapping(usage):
        return None
    return _usage_from_mapping(usage)


def _usage_from_responses_payload(payload: Mapping[str, JsonValue]) -> SourceUsage | None:
    usage = payload.get("usage")
    if not is_json_mapping(usage):
        return None
    return _usage_from_responses_mapping(usage)


def _usage_from_embeddings_payload(payload: Mapping[str, JsonValue]) -> SourceUsage | None:
    """Embeddings responses report prompt/total tokens and no completion tokens."""
    usage = payload.get("usage")
    if not is_json_mapping(usage):
        return None
    return _usage_from_mapping(usage) or _usage_from_total_tokens_mapping(usage)


def _usage_from_audio_body(body: bytes, content_type: str | None) -> SourceUsage | None:
    if not _is_json_content_type(content_type):
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError):
        return None
    if not is_json_mapping(parsed):
        return None
    usage = parsed.get("usage")
    if not is_json_mapping(usage):
        return None
    return _usage_from_mapping(usage) or _usage_from_responses_mapping(usage) or _usage_from_total_tokens_mapping(usage)


def _audio_seconds_from_body(body: bytes, content_type: str | None) -> float | None:
    """Extract transcribed audio length in seconds from a JSON transcription body.

    Recognizes the top-level ``duration`` field emitted by OpenAI
    ``verbose_json`` and most Whisper-compatible servers, and a
    ``usage.seconds`` / ``usage.duration`` fallback. Non-positive or
    non-numeric values yield ``None`` so duration billing fails closed.
    """
    if not _is_json_content_type(content_type):
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError):
        return None
    if not is_json_mapping(parsed):
        return None
    candidate = parsed.get("duration")
    if candidate is None:
        usage = parsed.get("usage")
        if is_json_mapping(usage):
            candidate = usage.get("seconds")
            if candidate is None:
                candidate = usage.get("duration")
    if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
        return None
    seconds = float(candidate)
    return seconds if seconds > 0 else None


def _timings_from_payload(payload: Mapping[str, JsonValue]) -> SourceTimings | None:
    metrics = payload.get("metrics")
    if not is_json_mapping(metrics):
        return None
    return _timings_from_metrics(metrics)


def _timings_from_audio_body(body: bytes, content_type: str | None) -> SourceTimings | None:
    if not _is_json_content_type(content_type):
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError):
        return None
    if not is_json_mapping(parsed):
        return None
    return _timings_from_payload(parsed)


def _timings_from_metrics(metrics: Mapping[str, JsonValue]) -> SourceTimings | None:
    """Parse vLLM-style per-request timing metrics into ``SourceTimings``.

    vLLM's OpenAI-compatible server (and compatible forks) can attach a
    ``metrics`` object alongside ``usage`` with ``time_to_first_token_ms``
    (TTFT) and ``generation_time_ms`` (wall-clock time to produce the
    completion tokens *after* the first token). Storing their sum as
    ``latency_ms`` alongside ``latency_first_token_ms`` lets the existing
    dashboard TPS calculation use ``generation_time_ms`` as its denominator,
    preserving the generation-only throughput semantics used for
    subscription-backed requests.
    """
    ttft = metrics.get("time_to_first_token_ms")
    generation = metrics.get("generation_time_ms")
    if isinstance(ttft, bool) or not isinstance(ttft, (int, float)):
        return None
    if isinstance(generation, bool) or not isinstance(generation, (int, float)):
        return None
    if (isinstance(ttft, float) and not isfinite(ttft)) or (isinstance(generation, float) and not isfinite(generation)):
        return None
    if ttft < 0 or generation < 0:
        return None
    return SourceTimings(
        latency_first_token_ms=round(ttft),
        latency_ms=round(ttft + generation),
    )


def _usage_from_mapping(usage: Mapping[str, JsonValue]) -> SourceUsage | None:
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        return None
    if prompt_tokens < 0 or completion_tokens < 0:
        # Fail closed: negative counts from a misbehaving source would reduce
        # API-key limit counters or record negative cost at settlement.
        return None
    cached_tokens = 0
    details = usage.get("prompt_tokens_details")
    if is_json_mapping(details):
        raw_cached = details.get("cached_tokens")
        cached_tokens = raw_cached if isinstance(raw_cached, int) else 0
    return SourceUsage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        cached_input_tokens=max(0, min(cached_tokens, prompt_tokens)),
    )


def _usage_from_responses_mapping(usage: Mapping[str, JsonValue]) -> SourceUsage | None:
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    if input_tokens < 0 or output_tokens < 0:
        # Fail closed: negative counts from a misbehaving source would reduce
        # API-key limit counters or record negative cost at settlement.
        return None
    cached_tokens = 0
    details = usage.get("input_tokens_details")
    if is_json_mapping(details):
        raw_cached = details.get("cached_tokens")
        cached_tokens = raw_cached if isinstance(raw_cached, int) else 0
    return SourceUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=max(0, min(cached_tokens, input_tokens)),
    )


def _usage_from_total_tokens_mapping(usage: Mapping[str, JsonValue]) -> SourceUsage | None:
    total_tokens = usage.get("total_tokens")
    if not isinstance(total_tokens, int) or total_tokens < 0:
        return None
    return SourceUsage(input_tokens=total_tokens, output_tokens=0)


def _is_json_content_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() in {"application/json", "text/json"}


class SourceStreamUsageParser:
    # A single SSE frame carrying usage is tiny; anything past this cap means
    # the upstream is not producing frame boundaries we recognize, and the
    # parser must not buffer the whole stream in memory.
    _MAX_BUFFER_CHARS = 1_048_576

    def __init__(self, usage_holder: SourceUsageHolder, *, response_shape: str) -> None:
        self._usage_holder = usage_holder
        self._response_shape = response_shape
        self._buffer = ""
        self._bom_pending = True
        self._cr_pending = False

    def feed(self, chunk: bytes) -> None:
        # SSE permits CRLF (and bare CR) line endings; normalize so frame
        # detection below only has to handle "\n\n". A CRLF split across two
        # chunks must stay one line ending: the CR that closed the previous
        # chunk was already normalized to "\n", so a chunk that opens with its
        # LF drops that LF -- otherwise the pair became "\n\n" and cut one
        # frame into two halves that parse to nothing while the event-block
        # reassembler delivers the whole event to the client (I11: delivered
        # => pinned; usage never captured).
        text = chunk.decode("utf-8", errors="ignore")
        if self._cr_pending:
            self._cr_pending = False
            text = text.removeprefix("\n")
        self._cr_pending = text.endswith("\r")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if self._bom_pending and text:
            # One optional leading UTF-8 BOM, ignored exactly as the public
            # wrapper's event-block reassembler ignores it: left in place it
            # turns the first field name into "\ufeffdata", so the parser would
            # observe nothing from a record the wrapper delivers to the client
            # (I11: delivered => pinned; usage never captured).
            self._bom_pending = False
            text = text.removeprefix("\ufeff")
        self._buffer += text
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            self._capture_frame(frame)
        if len(self._buffer) > self._MAX_BUFFER_CHARS:
            # The frame under construction outgrew the cap: its truncated
            # remainder will never parse, so it can no longer be classified by
            # its event type. No bookkeeping envelope (``response.created`` /
            # ``in_progress`` / ``queued``) is this large, so it is treated as
            # content (I11: delivered => pinned) -- a pre-content hook must run
            # before any byte of it reaches the client, and the withheld buffer
            # stays bounded by the cap instead of the whole stream.
            if self._response_shape == "responses":
                self._usage_holder.first_content_seen = True
            self._buffer = self._buffer[-self._MAX_BUFFER_CHARS :]

    def finish(self) -> None:
        """Parse the unterminated tail at EOF as a final frame.

        SSE consumers (the proxy's own event-block reassembler included)
        accept a final record that the source closed without the trailing
        blank line, so its event type, usage and terminal kind must reach the
        holder like any other frame; a truncated oversized remainder never
        starts with ``data:`` and parses to nothing. Idempotent.
        """

        tail, self._buffer = self._buffer, ""
        if tail.strip():
            self._capture_frame(tail)

    def _capture_frame(self, frame: str) -> None:
        # Join multi-line ``data:`` fields through the same reconstruction the
        # public wrapper uses (``extract_sse_data`` -> ``parse_sse_data_json``),
        # so the parser and the wrapper never disagree on the event inside one
        # pipeline: a source that splits a data JSON across several ``data:``
        # lines (legal SSE) must still set the holder observations the branch's
        # billing and continuity decisions key on (I11), not parse only the
        # first line into a ``ValueError`` and observe nothing.
        data = extract_sse_data(frame)
        if data is None:
            return
        try:
            parsed = json.loads(data)
        except ValueError:
            return
        if not isinstance(parsed, dict):
            return
        if self._response_shape == "responses":
            usage = _usage_from_responses_event(parsed)
            timings = _timings_from_responses_event(parsed)
            self._observe_responses_event(parsed)
        else:
            usage = _usage_from_chat_payload(parsed)
            timings = _timings_from_payload(parsed)
        if usage is not None:
            self._usage_holder.usage = usage
        if timings is not None:
            self._usage_holder.timings = timings

    def _observe_responses_event(self, event: dict[str, JsonValue]) -> None:
        """Record the frame observations the dispatch owner needs (design v3 §6).

        One set membership on the event ``type`` plus a ``len()`` for delta
        frames on top of the ``json.loads`` the usage capture already paid;
        ``event`` is that ``json.loads`` result, owned by this parser and read
        only, so it is handed to the classifier without a copy.
        """

        holder = self._usage_holder
        # The wrapper's classifier: a string ``type`` wins and a typeless record
        # carrying an ``error`` object is the ``error`` terminal (the wrapper
        # relays or rewrites it as a failure; the parser must not read it as
        # bookkeeping that is withheld ahead of the hook or settled as a cancel).
        event_type = classify_event_type(event)
        kind = classify_responses_frame(event_type)
        response = event.get("response")
        if is_json_mapping(response):
            response_id = response.get("id")
            if isinstance(response_id, str) and (holder.response_id is None or event_type == "response.created"):
                holder.response_id = response_id
            if event_type == "response.created" and holder.created_envelope is None:
                holder.created_envelope = dict(response)
        if kind == "non_content":
            return
        if kind == "failure_terminal":
            holder.terminal_kind = "error" if event_type == "error" else "failed"
            return
        holder.first_content_seen = True
        if kind == "success_terminal":
            holder.terminal_kind = "incomplete" if event_type == "response.incomplete" else "completed"
            return
        assert event_type is not None
        if event_type.startswith("response.output_item."):
            holder.first_output_item_seen = True
        elif event_type.endswith(".delta"):
            delta = event.get("delta")
            if isinstance(delta, str):
                holder.delta_chars += len(delta)


def _usage_from_responses_event(payload: Mapping[str, JsonValue]) -> SourceUsage | None:
    response = payload.get("response")
    usage = _usage_from_responses_payload(response) if is_json_mapping(response) else None
    if usage is None:
        usage = _usage_from_responses_payload(payload)
    return usage


def _timings_from_responses_event(payload: Mapping[str, JsonValue]) -> SourceTimings | None:
    response = payload.get("response")
    timings = _timings_from_payload(response) if is_json_mapping(response) else None
    if timings is None:
        timings = _timings_from_payload(payload)
    return timings
