from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, NoReturn, Protocol

import anyio

from app.core.auth.refresh import RefreshError, is_transient_refresh_contention, refresh_contention_kind
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.model_registry import get_model_registry
from app.core.openai.models import OpenAIEvent
from app.core.plan_types import account_plan_matches_allowed
from app.core.resilience.network_recovery import PROCESS_NETWORK_UNAVAILABLE_CODE
from app.core.resilience.overload import is_local_overload_error_code
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.db.models import Account
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)
from app.modules.proxy.affinity import _AffinityPolicy
from app.modules.proxy.load_balancer import (
    AccountLease,
    AccountSelection,
    CatalogOmissionQuotaAdmission,
)
from app.modules.proxy.tool_call_dedupe import ToolCallDedupeKey
from app.modules.proxy.work_admission import AdmissionLease

logger = logging.getLogger(__name__)

_REQUEST_TRANSPORT_HTTP = "http"
_REQUEST_TRANSPORT_WEBSOCKET = "websocket"
_REASONING_SUMMARY_BLANK_HTML_COMMENT_RE = re.compile(r"(?m)^[ \t]*<!--\s*-->[ \t]*(?:\r?\n|\Z)")
_TTFT_EVENT_TYPES = frozenset(
    {
        "response.output_text.delta",
        "response.refusal.delta",
        "response.reasoning_text.delta",
    }
)
_TTFT_TOOL_DELTA_EVENT_TYPES = frozenset({"response.function_call_arguments.delta", "response.output_tool_call.delta"})
_TTFT_REASONING_EVENT_TYPES = frozenset(
    {"response.reasoning_summary_text.delta", "response.reasoning_summary_text.done"}
)
_PENDING_TOOL_CALL_OUTPUT_ITEM_TYPE_BY_CALL_TYPE = {
    "function_call": "function_call_output",
    "custom_tool_call": "custom_tool_call_output",
    "apply_patch_call": "apply_patch_call_output",
}
_PENDING_TOOL_CALL_ITEM_TYPES = frozenset(_PENDING_TOOL_CALL_OUTPUT_ITEM_TYPE_BY_CALL_TYPE)
_PENDING_TOOL_CALL_OUTPUT_ITEM_TYPES = frozenset(_PENDING_TOOL_CALL_OUTPUT_ITEM_TYPE_BY_CALL_TYPE.values())
_TTFT_OUTPUT_ITEM_TYPES = _PENDING_TOOL_CALL_ITEM_TYPES - {"function_call"}
_WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS = 20
_WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS = 0.05
_HARD_HTTP_BRIDGE_AFFINITY_KINDS = frozenset(
    {
        "turn_state_header",
        "session_header",
        "internal_unanchored_parallel",
        "internal_model_parallel",
        "internal_request_parallel",
    }
)
_ACCOUNT_SELECTION_RECOVERY_MIN_SLEEP_SECONDS = 1.0
_ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS = 30.0
_ACCOUNT_SELECTION_RECOVERY_MAX_SLEEP_SECONDS = 300.0
_ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS = 10.0
_ACCOUNT_SELECTION_RETRY_HINT_RE = re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)
_LOCAL_ACCOUNT_CAP_ERROR_CODES = frozenset({"account_response_create_cap", "account_stream_cap"})
_ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE = "account_model_unsupported"
_PROPAGATED_CAPACITY_STARTUP_WAIT: ContextVar[asyncio.Event | None] = ContextVar(
    "propagated_capacity_startup_wait",
    default=None,
)
_PROPAGATED_CAPACITY_STARTUP_READY: ContextVar[asyncio.Event | None] = ContextVar(
    "propagated_capacity_startup_ready",
    default=None,
)


def _strip_blank_html_comment_lines(text: str) -> str:
    terminal_match = None
    for match in _REASONING_SUMMARY_BLANK_HTML_COMMENT_RE.finditer(text):
        if match.end() == len(text):
            terminal_match = match
    cleaned, count = _REASONING_SUMMARY_BLANK_HTML_COMMENT_RE.subn("", text)
    if count == 0:
        return text
    if terminal_match is not None:
        return cleaned.rstrip("\r\n")
    return cleaned


def _reasoning_summary_delta_key(payload: Mapping[str, JsonValue]) -> tuple[str | None, int | None, int | None]:
    item_id = payload.get("item_id")
    output_index = payload.get("output_index")
    summary_index = payload.get("summary_index")
    return (
        item_id if isinstance(item_id, str) else None,
        output_index if isinstance(output_index, int) else None,
        summary_index if isinstance(summary_index, int) else None,
    )


def _could_be_blank_html_comment_line(text: str) -> bool:
    candidate = text.rsplit("\n", 1)[-1].lstrip(" \t")
    if not candidate:
        return False
    if not candidate.startswith("<!--"):
        return "<!--".startswith(candidate)
    remainder = candidate[4:].lstrip()
    if not remainder.startswith("-->"):
        return "-->".startswith(remainder)
    return not remainder[3:].strip(" \t\r")


def _is_reasoning_summary_interleavable_event(event_type: JsonValue | None) -> bool:
    return isinstance(event_type, str) and (event_type == "response.in_progress" or event_type.startswith("codex."))


@dataclass(slots=True)
class _TTFTReasoningDeltaState:
    text: str
    visible_at: float | None = None


def _visible_reasoning_prefix_before_blank_comment_candidate(text: str) -> str:
    if "\n" not in text:
        return ""
    prefix, _candidate = text.rsplit("\n", 1)
    return _strip_blank_html_comment_lines(prefix).strip()


def _finalize_ttft_reasoning_deltas(
    pending_reasoning_deltas: dict[tuple[str | None, int | None, int | None], _TTFTReasoningDeltaState],
) -> float | None:
    visible_at_values: list[float] = []
    now: float | None = None
    for pending in pending_reasoning_deltas.values():
        if not _strip_blank_html_comment_lines(pending.text):
            continue
        if pending.visible_at is not None:
            visible_at_values.append(pending.visible_at)
            continue
        if now is None:
            now = time.monotonic()
        visible_at_values.append(now)
    pending_reasoning_deltas.clear()
    return min(visible_at_values) if visible_at_values else None


def _ttft_event_visible_at(
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    pending_reasoning_deltas: dict[tuple[str | None, int | None, int | None], _TTFTReasoningDeltaState] | None = None,
) -> float | None:
    now = time.monotonic()
    pending = pending_reasoning_deltas if pending_reasoning_deltas is not None else {}
    event_key = _reasoning_summary_delta_key(payload) if payload is not None else (None, None, None)
    if (
        pending
        and not _is_reasoning_summary_interleavable_event(event_type)
        and not (event_type in _TTFT_REASONING_EVENT_TYPES and event_key in pending)
    ):
        visible_at = _finalize_ttft_reasoning_deltas(pending)
        if visible_at is not None:
            return visible_at
    if event_type == "response.reasoning_summary_text.delta":
        delta = payload.get("delta") if payload is not None else None
        if not isinstance(delta, str):
            return None
        previous = pending.pop(event_key, None)
        combined = (previous.text if previous is not None else "") + delta
        cleaned = _strip_blank_html_comment_lines(combined)
        if cleaned != combined:
            if not cleaned:
                return None
            return previous.visible_at if previous is not None and previous.visible_at is not None else now
        if _could_be_blank_html_comment_line(combined):
            visible_at = None if previous is None else previous.visible_at
            if visible_at is None and _visible_reasoning_prefix_before_blank_comment_candidate(combined):
                visible_at = now
            pending[event_key] = _TTFTReasoningDeltaState(combined, visible_at=visible_at)
            return None
        if not combined:
            return None
        return previous.visible_at if previous is not None and previous.visible_at is not None else now
    if event_type == "response.reasoning_summary_text.done" and event_key in pending:
        visible_at = _finalize_ttft_reasoning_deltas({event_key: pending.pop(event_key)})
        return visible_at
    if event_type in _TTFT_TOOL_DELTA_EVENT_TYPES:
        if payload is None:
            return None
        has_visible_part = any(
            isinstance(payload.get(key), str) and bool(payload[key]) for key in ("delta", "arguments")
        )
        return now if has_visible_part else None
    if event_type in _TTFT_EVENT_TYPES:
        delta = payload.get("delta") if payload is not None else None
        return now if isinstance(delta, str) and bool(delta) else None
    if event_type != "response.output_item.added" or not isinstance(payload, dict):
        return None
    item = payload.get("item")
    return now if isinstance(item, dict) and item.get("type") in _TTFT_OUTPUT_ITEM_TYPES else None


def _is_ttft_event(
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    pending_reasoning_deltas: dict[tuple[str | None, int | None, int | None], _TTFTReasoningDeltaState] | None = None,
) -> bool:
    return _ttft_event_visible_at(event_type, payload, pending_reasoning_deltas) is not None


def _ttft_latency_ms_from_visible_at(visible_at: float | None, started_at: float) -> int | None:
    return None if visible_at is None else max(0, int((visible_at - started_at) * 1000))


def _ttft_event_latency_ms(
    event_type: str | None,
    payload: dict[str, JsonValue] | None,
    pending_reasoning_deltas: dict[tuple[str | None, int | None, int | None], _TTFTReasoningDeltaState],
    started_at: float,
) -> int | None:
    return _ttft_latency_ms_from_visible_at(
        _ttft_event_visible_at(event_type, payload, pending_reasoning_deltas), started_at
    )


def _finalize_ttft_latency_ms(
    pending_reasoning_deltas: dict[tuple[str | None, int | None, int | None], _TTFTReasoningDeltaState],
    started_at: float,
) -> int | None:
    return _ttft_latency_ms_from_visible_at(_finalize_ttft_reasoning_deltas(pending_reasoning_deltas), started_at)


def _bind_propagated_capacity_startup_wait(event: asyncio.Event) -> Token[asyncio.Event | None]:
    return _PROPAGATED_CAPACITY_STARTUP_WAIT.set(event)


def _reset_propagated_capacity_startup_wait(token: Token[asyncio.Event | None]) -> None:
    _PROPAGATED_CAPACITY_STARTUP_WAIT.reset(token)


def _bind_propagated_capacity_startup_ready(event: asyncio.Event) -> Token[asyncio.Event | None]:
    return _PROPAGATED_CAPACITY_STARTUP_READY.set(event)


def _reset_propagated_capacity_startup_ready(token: Token[asyncio.Event | None]) -> None:
    _PROPAGATED_CAPACITY_STARTUP_READY.reset(token)


def _signal_propagated_capacity_startup_wait() -> None:
    ready_event = _PROPAGATED_CAPACITY_STARTUP_READY.get()
    if ready_event is not None:
        ready_event.clear()
    event = _PROPAGATED_CAPACITY_STARTUP_WAIT.get()
    if event is not None:
        event.set()


def _signal_propagated_capacity_startup_ready() -> None:
    wait_event = _PROPAGATED_CAPACITY_STARTUP_WAIT.get()
    if wait_event is not None:
        wait_event.clear()
    event = _PROPAGATED_CAPACITY_STARTUP_READY.get()
    if event is not None:
        event.set()


def _account_selection_recovery_sleep_seconds_from_message(
    message: str | None,
    *,
    error_code: str | None = None,
    suppress_uncoded_local_selector_hint: bool = False,
) -> float | None:
    message = (message or "").strip()
    if not message:
        return None

    lowered = message.lower()
    if (
        "require re-authentication" in lowered
        or "all accounts are paused" in lowered
        or "no accounts with a plan" in lowered
        or "no accounts with available additional quota" in lowered
        or "no fresh additional quota data" in lowered
        or (
            (error_code == "no_accounts" or (suppress_uncoded_local_selector_hint and error_code is None))
            and lowered.startswith("rate limit exceeded. try again in")
        )
    ):
        return None

    retry_hint = _ACCOUNT_SELECTION_RETRY_HINT_RE.search(message)
    if retry_hint is not None:
        try:
            hinted_seconds = float(retry_hint.group(1))
        except ValueError:
            hinted_seconds = _ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS
        return min(
            max(hinted_seconds, _ACCOUNT_SELECTION_RECOVERY_MIN_SLEEP_SECONDS),
            _ACCOUNT_SELECTION_RECOVERY_MAX_SLEEP_SECONDS,
        )

    if "hit your spend cap set by the owner of your workspace" in lowered:
        return _ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS

    if error_code in _LOCAL_ACCOUNT_CAP_ERROR_CODES:
        return _ACCOUNT_SELECTION_RECOVERY_DEFAULT_SLEEP_SECONDS

    return None


def _account_selection_recovery_sleep_seconds(selection: AccountSelection) -> float | None:
    return _account_selection_recovery_sleep_seconds_from_message(
        selection.error_message,
        error_code=selection.error_code,
        suppress_uncoded_local_selector_hint=selection.account is None,
    )


def _account_capacity_wait_payload(
    request_state: "_WebSocketRequestState | None",
    *,
    request_id: str | None,
    reason: str | None,
    retry_after_seconds: float | None,
    started_at: float | None = None,
) -> dict[str, JsonValue]:
    wait_started_at = request_state.account_capacity_wait_started_at if request_state is not None else started_at
    waited_seconds = int(max(0.0, time.monotonic() - wait_started_at)) if wait_started_at is not None else 0
    payload: dict[str, JsonValue] = {
        "type": "codex.keepalive",
        "status": "waiting_for_account_capacity",
        "request_id": request_id or (request_state.request_id if request_state is not None else None),
        "waited_seconds": waited_seconds,
    }
    if reason:
        payload["reason"] = reason
    if retry_after_seconds is not None:
        payload["retry_after_seconds"] = int(max(0.0, retry_after_seconds))
    return payload


async def _sleep_for_account_selection_recovery(
    selection: AccountSelection,
    *,
    request_id: str | None,
    kind: str,
    request_stage: str,
    model: str | None,
    max_sleep_seconds: float | None = None,
    request_state: "_WebSocketRequestState | None" = None,
    heartbeat: Callable[[float], Awaitable[None]] | None = None,
) -> bool:
    sleep_seconds = _account_selection_recovery_sleep_seconds(selection)
    if sleep_seconds is None:
        return False
    if max_sleep_seconds is not None:
        if max_sleep_seconds <= 0:
            return False
        sleep_seconds = min(sleep_seconds, max_sleep_seconds)

    if request_state is not None:
        request_state.account_capacity_waiting = True
        request_state.account_capacity_wait_reason = selection.error_message
        request_state.account_capacity_wait_started_at = (
            request_state.account_capacity_wait_started_at or time.monotonic()
        )
        request_state.account_capacity_wait_retry_after_seconds = sleep_seconds

    logger.info(
        "Waiting for an account to recover before retrying selection request_id=%s kind=%s "
        "request_stage=%s model=%s sleep_seconds=%.1f error=%s",
        request_id,
        kind,
        request_stage,
        model,
        sleep_seconds,
        selection.error_message,
    )
    remaining = sleep_seconds
    try:
        while remaining > 0:
            if heartbeat is not None:
                await heartbeat(remaining)
            chunk = min(remaining, _ACCOUNT_SELECTION_RECOVERY_HEARTBEAT_SECONDS)
            await asyncio.sleep(chunk)
            remaining -= chunk
    finally:
        if request_state is not None:
            request_state.account_capacity_waiting = False
            request_state.account_capacity_wait_reason = None
            request_state.account_capacity_wait_retry_after_seconds = None
    return True


async def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> Any:
    return await func(*args, **_supported_optional_kwargs(func, optional_kwargs, required_kwargs))


def _supported_optional_kwargs(
    func: Callable[..., Any],
    optional_kwargs: Mapping[str, Any],
    required_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    kwargs = dict(required_kwargs)
    kwargs.update(optional_kwargs)
    if optional_kwargs:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            signature = None
        accepts_var_keyword = signature is not None and any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
        )
        if signature is not None and not accepts_var_keyword:
            for name in optional_kwargs:
                if name not in signature.parameters:
                    kwargs.pop(name, None)
    return kwargs


def _request_log_useragent_fields(headers: Mapping[str, str]) -> tuple[str | None, str | None]:
    raw_useragent = next((value for key, value in headers.items() if key.lower() == "user-agent"), None)
    if raw_useragent is None:
        return None, None
    useragent = raw_useragent.strip()
    if not useragent:
        return None, None
    first_token = useragent.split(maxsplit=1)[0]
    useragent_group = first_token.split("/", 1)[0].strip() or None
    return useragent, useragent_group


class _RetryableStreamError(Exception):
    def __init__(self, code: str, error: UpstreamError, *, exclude_account: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.error = error
        self.exclude_account = exclude_account


class _WebSocketConnectFailureEmitted(Exception):
    pass


class _WebSocketTransientRefreshFailover(Exception):
    """Signals the WebSocket connect loop to fail over after a transient refresh.

    Raised from the connect attempt when a non-permanent, transport-level
    ``RefreshError`` (for example ``refresh_claim_timeout`` when another replica
    holds the account's refresh claim) reaches the connect path and the request
    is not bound to a preferred/required account. The loop releases the skipped
    account's stream lease, excludes it, and reselects a healthy account instead
    of surfacing a bogus 401 ``invalid_api_key``.
    """

    def __init__(self, account_id: str) -> None:
        super().__init__(account_id)
        self.account_id = account_id


# ---- Refresh-claim contention failover helpers -----------------------------
#
# A transient cross-replica refresh contention (``is_transient_refresh_contention``:
# ONLY the benign ``refresh_claim_timeout`` claim-contention code and the
# post-exchange ``token_persist_conflict`` / ``status_downgrade_conflict`` CAS
# codes, NOT the broad ``transport_error`` flag) means the account's credentials
# are healthy and only its refresh claim/persist lost to a peer replica. These
# helpers let the proxy previsible-unary failover surface a retryable
# ``upstream_unavailable`` WITHOUT recording an account-health penalty, while a
# genuine OAuth transport failure keeps its normal health accounting.

_FAILED_ACCOUNT_ATTR = "_codex_lb_failed_account"
_CLAIM_CONTENTION_UNPENALIZED_ATTR = "_codex_lb_claim_contention_unpenalized"


class _RefreshFailoverProxy(Protocol):
    async def _handle_stream_error(
        self, account: Account, error: Any, code: str, http_status: int | None = None
    ) -> Any: ...


def is_claim_contention_unpenalized(exc: object) -> bool:
    """True when ``exc`` is a retryable ``upstream_unavailable`` from claim contention.

    The account is healthy (a peer replica held its refresh claim), so callers
    MUST NOT record an account-health penalty for it.
    """
    return bool(getattr(exc, _CLAIM_CONTENTION_UNPENALIZED_ATTR, False))


def raise_proxy_unavailable_for_claim_contention(message: str, account: Account) -> NoReturn:
    """Raise a retryable ``upstream_unavailable`` marked as (unpenalized) claim contention."""
    exc = ProxyResponseError(502, openai_error("upstream_unavailable", message))
    setattr(exc, _FAILED_ACCOUNT_ATTR, account)
    setattr(exc, _CLAIM_CONTENTION_UNPENALIZED_ATTR, True)
    raise exc


def _raise_proxy_unavailable_for_account(message: str, account: Account) -> NoReturn:
    exc = ProxyResponseError(502, openai_error("upstream_unavailable", message))
    setattr(exc, _FAILED_ACCOUNT_ATTR, account)
    raise exc


async def failover_after_previsible_refresh_error(
    proxy: _RefreshFailoverProxy,
    exc: Exception,
    current: Account,
    *,
    attempt: int,
    max_account_attempts: int,
    strict_account_id: str | None,
    excluded_account_ids: set[str],
    select_next_account: Callable[[set[str]], Awaitable[AccountSelection]],
    request_id: str,
    kind: str,
) -> Account:
    """Pick the next account after a previsible-unary freshness/connect failure.

    Consolidates the transient ``RefreshError`` claim-contention and transport
    paths plus raw aiohttp/connect errors into one decision:

    * Transient refresh contention (``is_transient_refresh_contention``) is ALWAYS
      retryable and NEVER penalizes the skipped account (its credentials are
      healthy; only its refresh claim/persist lost to a peer). This covers BOTH
      benign claim contention and a post-exchange persist/status CAS conflict; the
      latter is logged distinctly (a rarer, more-serious internal race). On
      strict-pin / attempt exhaustion or an empty selection it raises a
      claim-contention-marked ``upstream_unavailable`` that the caller's terminal
      ``_handle_proxy_error`` skips penalizing.
    * A genuine OAuth ``transport_error`` refresh failure or a raw aiohttp/connect
      failure keeps its health accounting: failover is gated on the message text
      and the skipped account is penalized via ``_handle_stream_error`` so a
      persistently broken account backs off.

    Returns the next account to try; raises otherwise.
    """
    from app.modules.proxy._service.streaming.helpers import _should_retry_transient_stream_error

    claim = isinstance(exc, RefreshError) and is_transient_refresh_contention(exc)
    contention_kind = refresh_contention_kind(exc) if isinstance(exc, RefreshError) else None
    message = getattr(exc, "message", None) or str(exc) or "Request to upstream timed out"
    if contention_kind == "persist_conflict":
        # Post-exchange guarded-write CAS conflict: rarer/more-serious than benign
        # claim contention, so surface it distinctly (it still fails over with no
        # penalty, identically to benign contention).
        logger.warning(
            "%s refresh post-exchange persist conflict code=%s request_id=%s account_id=%s",
            kind,
            getattr(exc, "code", None),
            request_id,
            current.id,
            exc_info=True,
        )
    else:
        logger.warning(
            "%s refresh %s request_id=%s account_id=%s",
            kind,
            "claim contention" if claim else "failed",
            request_id,
            current.id,
            exc_info=True,
        )
    if not claim and not _should_retry_transient_stream_error("upstream_unavailable", message):
        _raise_proxy_unavailable_for_account(message, current)

    def _give_up() -> NoReturn:
        if claim:
            raise_proxy_unavailable_for_claim_contention(message, current)
        _raise_proxy_unavailable_for_account(message, current)

    if (strict_account_id is not None and current.id == strict_account_id) or attempt >= max_account_attempts:
        _give_up()
    excluded_account_ids.add(current.id)
    selection = await select_next_account(excluded_account_ids)
    selected_account = selection.account
    if selected_account is None:
        _give_up()
    assert selected_account is not None
    if not claim:
        # Genuine transport / connect failure: penalize the skipped account so a
        # persistently broken account backs off. Claim contention never penalizes.
        await proxy._handle_stream_error(current, {"message": message}, "upstream_unavailable")
    return selected_account


class _TransientStreamError(Exception):
    """Transient upstream error (e.g. 500 server_error) - retry on same account first."""

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
class _ApiKeyReservationTouchState:
    last_touch_at: float


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
    downstream_visible: bool = False
    downstream_text_visible: bool = False
    response_id: str | None = None
    usage_settlement_transferred: bool = False

    def reset(self) -> None:
        fresh = type(self)()
        self.__dict__.update(fresh.__dict__)


def _stream_settlement_error_payload(settlement: _StreamSettlement) -> UpstreamError:
    if settlement.error is not None:
        return settlement.error
    payload: UpstreamError = {}
    if settlement.error_message:
        payload["message"] = settlement.error_message
    else:
        payload["message"] = "Upstream error"
    return payload


def _consume_api_key_reservation_heartbeat_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.warning("API key reservation heartbeat task failed during cancellation", exc_info=True)


@dataclass(frozen=True, slots=True)
class _FilePinEntry:
    account_id: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class _RequestLogFailureMetadata:
    failure_phase: str | None = None
    failure_detail: str | None = None
    failure_exception_type: str | None = None
    upstream_status_code: int | None = None
    upstream_error_code: str | None = None
    bridge_stage: str | None = None


@dataclass
class _WebSocketRequestState:
    request_id: str
    model: str | None
    service_tier: str | None
    reasoning_effort: str | None
    api_key_reservation: ApiKeyUsageReservationData | None
    started_at: float
    responses_lite_model: str | None = None
    latency_first_token_ms: int | None = None
    ttft_reasoning_deltas: dict[tuple[str | None, int | None, int | None], _TTFTReasoningDeltaState] = field(
        default_factory=dict
    )
    latency_response_created_ms: int | None = None
    latency_first_upstream_event_ms: int | None = None
    latency_response_create_gate_wait_ms: int | None = None
    latency_bridge_queue_wait_ms: int | None = None
    response_create_gate_wait_started_at: float | None = None
    bridge_queue_wait_started_at: float | None = None
    # Monotonic deadline of the original bridge request budget. Retry and
    # recovery paths re-prepare request states with a fresh started_at, so
    # budget clamps must use this instead of recomputing from started_at.
    bridge_request_deadline: float | None = None
    prewarm_status: str | None = None
    prewarm_latency_ms: int | None = None
    session_previous_gap_ms: int | None = None
    request_log_id: str | None = None
    archive_request_id: str | None = None
    requested_service_tier: str | None = None
    actual_service_tier: str | None = None
    response_id: str | None = None
    awaiting_response_created: bool = False
    event_queue: asyncio.Queue[str | None] | None = None
    transport: str = _REQUEST_TRANSPORT_WEBSOCKET
    upstream_transport: str | None = _REQUEST_TRANSPORT_WEBSOCKET
    enforce_openai_sdk_contract: bool = True
    request_kind: str = "normal"
    generate_false_prewarm: bool = False
    api_key: ApiKeyData | None = None
    request_usage_budget: ApiKeyRequestUsageBudget | None = None
    request_text: str | None = None
    replay_count: int = 0
    auth_replay_count: int = 0
    auth_replay_counts_by_account: dict[str, int] = field(default_factory=dict)
    force_refresh_account_id: str | None = None
    excluded_account_ids: set[str] = field(default_factory=set)
    # A narrowly classified pre-acceptance account/model rejection may move
    # once to another account. Keep the original upstream error until that
    # replacement connection is established so an empty replacement pool does
    # not turn the upstream 400 into a proxy-generated 5xx/no-accounts error.
    precreated_replay_reason: str | None = None
    precreated_replay_account_id: str | None = None
    skip_request_log: bool = False
    previous_response_id: str | None = None
    session_id: str | None = None
    proxy_injected_previous_response_id: bool = False
    expose_stale_previous_response_classifier: bool = False
    fresh_upstream_request_text: str | None = None
    # True only when ``fresh_upstream_request_text`` contains a *safe* pre-
    # injection form of this request that can be replayed as a fresh turn.
    # Durable-anchor injection captures the original unanchored full-resend
    # payload, so dropping the anchor and replaying is equivalent to the
    # client's own retry. Session-level anchor injection does not set this:
    # the original payload may have omitted history the conversation depended
    # on, and dropping the anchor there would silently turn a continuation into
    # a context-free fresh turn.
    fresh_upstream_request_is_retry_safe: bool = False
    # Responses-Lite model advertised by ``fresh_upstream_request_text``. A
    # fresh replay built from a trusted marker-only frame has the reserved
    # marker stripped, so swapping to the fresh body must also swap this onto
    # ``responses_lite_model``; otherwise the replay's ``response.created``
    # would be recorded as a Lite acceptance for a non-Lite upstream request.
    fresh_upstream_request_responses_lite_model: str | None = None
    request_stage: str = "first_turn"
    preferred_account_id: str | None = None
    require_security_work_authorized: bool = False
    file_required_preferred_account: bool = False
    bridge_soft_capacity_reroute_allowed: bool = False
    error_code_override: str | None = None
    error_message_override: str | None = None
    error_type_override: str | None = None
    error_param_override: str | None = None
    failure_phase_override: str | None = None
    failure_detail_override: str | None = None
    upstream_error_code_override: str | None = None
    error_http_status_override: int | None = None
    response_event_count: int = 0
    previous_response_not_found_rewritten: bool = False
    previous_response_owner_lookup_source: str | None = None
    previous_response_owner_lookup_outcome: str | None = None
    previous_response_owner_requested_at: datetime | None = None
    previous_response_owner_session_id: str | None = None
    response_create_gate_acquired: bool = False
    response_create_gate: asyncio.Semaphore | None = None
    response_create_admission: AdmissionLease | None = None
    account_response_create_lease: AccountLease | None = None
    account_response_create_release: Callable[[AccountLease | None], Coroutine[Any, Any, None]] | None = None
    websocket_stream_lease: AccountLease | None = None
    affinity_policy: _AffinityPolicy = field(default_factory=_AffinityPolicy)
    suppressed_downstream_tool_call: bool = False
    suppressed_duplicate_tool_call: bool = False
    pending_function_call_ids: list[str] = field(default_factory=list)
    pending_tool_call_types: dict[str, str] = field(default_factory=dict)
    seen_tool_call_keys: dict[ToolCallDedupeKey, None] = field(default_factory=dict)
    input_item_count: int = 0
    input_full_fingerprint: str | None = None
    api_key_reservation_last_touch_at: float = field(default_factory=time.monotonic)
    api_key_reservation_heartbeat_stop: asyncio.Event | None = None
    api_key_reservation_heartbeat_task: asyncio.Task[None] | None = None
    upstream_proxy_route_mode: str | None = None
    upstream_proxy_pool_id: str | None = None
    upstream_proxy_endpoint_id: str | None = None
    upstream_proxy_fallback_used: bool | None = None
    upstream_proxy_fail_closed_reason: str | None = None
    useragent: str | None = None
    useragent_group: str | None = None
    client_ip: str | None = None
    downstream_visible: bool = False
    last_downstream_sequence_number: int | None = None
    suppress_next_created_downstream: bool = False
    replay_downstream_response_id: str | None = None
    draining_until_terminal: bool = False
    account_capacity_waiting: bool = False
    account_capacity_wait_reason: str | None = None
    account_capacity_wait_started_at: float | None = None
    account_capacity_wait_retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class _HTTPBridgeSessionKey:
    affinity_kind: str
    affinity_key: str
    api_key_id: str | None
    strength: Literal["hard", "soft"] | None = None

    def __post_init__(self) -> None:
        strength = self.strength
        if strength is None:
            strength = "hard" if self.affinity_kind in _HARD_HTTP_BRIDGE_AFFINITY_KINDS else "soft"
        object.__setattr__(self, "strength", strength)


@dataclass(frozen=True, slots=True)
class _HTTPBridgeOwnerForward:
    owner_instance: str
    owner_endpoint: str
    key: _HTTPBridgeSessionKey


@dataclass(slots=True)
class _HTTPBridgeSession:
    key: _HTTPBridgeSessionKey
    headers: dict[str, str]
    affinity: _AffinityPolicy
    request_model: str | None
    account: Account
    upstream: UpstreamResponsesWebSocket
    upstream_control: _WebSocketUpstreamControl
    pending_requests: deque[_WebSocketRequestState]
    pending_lock: anyio.Lock
    response_create_gate: asyncio.Semaphore
    queued_request_count: int
    last_used_at: float
    idle_ttl_seconds: float
    unanchored_reservation_id: str | None = None
    admission_waiter_count: int = 0
    request_service_tier: str | None = None
    catalog_omission_quota_admission: CatalogOmissionQuotaAdmission | None = None
    lifecycle_lock: anyio.Lock = field(default_factory=anyio.Lock)
    api_key: ApiKeyData | None = None
    codex_session: bool = False
    prewarmed: bool = False
    prewarm_lock: anyio.Lock | None = None
    upstream_turn_state: str | None = None
    downstream_turn_state: str | None = None
    downstream_turn_state_aliases: set[str] = field(default_factory=set)
    previous_response_ids: set[str] = field(default_factory=set)
    alias_registration_generation: int = 0
    turn_state_alias_registration_generations: dict[str, int] = field(default_factory=dict)
    previous_response_alias_registration_generations: dict[str, int] = field(default_factory=dict)
    last_completed_input_count: int = 0
    last_completed_response_id: str | None = None
    last_completed_input_prefix_fingerprint: str | None = None
    last_pending_tool_calls: dict[str, str] = field(default_factory=dict)
    durable_session_id: str | None = None
    durable_owner_epoch: int | None = None
    upstream_reader: asyncio.Task[None] | None = None
    last_upstream_close_code: int | None = None
    closed: bool = False
    account_lease: AccountLease | None = None
    upstream_close_attempted: bool = False
    seen_tool_call_keys: dict[ToolCallDedupeKey, None] = field(default_factory=dict)
    upstream_proxy_route_mode: str | None = None
    upstream_proxy_pool_id: str | None = None
    upstream_proxy_endpoint_id: str | None = None
    upstream_proxy_fallback_used: bool | None = None
    upstream_proxy_fail_closed_reason: str | None = None


def _http_bridge_session_supports_service_tier(
    session: _HTTPBridgeSession,
    *,
    request_model: str | None,
    request_service_tier: str | None,
) -> bool:
    if request_model is None:
        return True

    registry = get_model_registry()
    # Mirror select_account: only apply model-account filtering when the model has
    # registry plan-presence. An operator-mapped but unadvertised slug yields an
    # authoritative-empty account catalog, which must not deny bridge session reuse.
    # Explicitly suppressed catalog slugs must, however, not allow reuse because
    # they intentionally block account selection even when the catalog is empty.
    plan_types_for_model = getattr(registry, "plan_types_for_model", None)
    model_allowed_plans = plan_types_for_model(request_model) if callable(plan_types_for_model) else None
    is_suppressed_model = getattr(registry, "is_suppressed_model", None)
    if callable(plan_types_for_model) and not model_allowed_plans:
        if callable(is_suppressed_model) and is_suppressed_model(request_model):
            return False
        return True
    account_indexes_cover_owner = True
    current_account_plan: str | None = None
    current_account_plan_is_authoritative = False
    get_snapshot = getattr(registry, "get_snapshot", None)
    if callable(get_snapshot):
        snapshot = get_snapshot()
        current_account_plan_is_authoritative = snapshot is not None and session.account.id in snapshot.account_plans
        account_indexes_cover_owner = current_account_plan_is_authoritative
        if current_account_plan_is_authoritative:
            current_account_plan = snapshot.account_plans[session.account.id]
    account_ids_for_model = getattr(registry, "account_ids_for_model", None)
    model_account_ids = (
        account_ids_for_model(request_model)
        if callable(account_ids_for_model) and account_indexes_cover_owner
        else None
    )
    model_catalog_omits_account = model_account_ids is not None and session.account.id not in model_account_ids
    quota_admission_matches = (
        session.catalog_omission_quota_admission is not None
        and session.catalog_omission_quota_admission.matches(
            requested_model=request_model,
            service_tier=request_service_tier,
        )
    )
    if model_catalog_omits_account and not quota_admission_matches:
        return False
    # Keep bridge reuse aligned with account selection: clients commonly send
    # these omit-equivalent values explicitly, but neither selects a specific
    # service tier.
    normalized_service_tier = request_service_tier.strip().lower() if request_service_tier is not None else None
    if normalized_service_tier in {None, "auto", "default"}:
        allowed_plans = model_allowed_plans
    else:
        allowed_account_ids = (
            registry.account_ids_for_model_service_tier(request_model, request_service_tier)
            if account_indexes_cover_owner
            else None
        )
        if allowed_account_ids is not None and not model_catalog_omits_account:
            return session.account.id in allowed_account_ids

        allowed_plans = registry.plan_types_for_model_service_tier(request_model, request_service_tier)
    if allowed_plans is None:
        return True
    if current_account_plan_is_authoritative:
        account_plan = current_account_plan
    elif session.catalog_omission_quota_admission is not None:
        # Quota admission proves model support, not the owner's current plan eligibility.
        return False
    else:
        account_plan = session.account.plan_type
    return account_plan_matches_allowed(account_plan, allowed_plans)


@dataclass(slots=True)
class _WebSocketContinuityState:
    last_completed_input_count: int = 0
    last_completed_response_id: str | None = None
    last_completed_input_prefix_fingerprint: str | None = None
    last_pending_function_call_ids: list[str] = field(default_factory=list)
    last_pending_tool_call_types: dict[str, str] = field(default_factory=dict)
    responses_lite_model: str | None = None
    responses_lite_response_id: str | None = None


@dataclass(frozen=True, slots=True)
class _WebSocketContinuityAnchor:
    previous_response_id: str
    stored_input_item_count: int


@dataclass(slots=True)
class _WebSocketUpstreamControl:
    reconnect_requested: bool = False
    retire_after_drain: bool = False
    suppress_downstream_event: bool = False
    replay_request_state: _WebSocketRequestState | None = None
    downstream_texts: list[str] | None = None
    downstream_sequence_request_state: _WebSocketRequestState | None = None
    downstream_sequence_number: int | None = None
    seen_tool_call_keys: dict[ToolCallDedupeKey, None] = field(default_factory=dict)


@dataclass(slots=True)
class _DownstreamWebSocketActivity:
    last_activity_at: float = field(default_factory=time.monotonic)
    disconnected: bool = False

    def mark(self) -> None:
        self.last_activity_at = time.monotonic()

    def mark_disconnected(self) -> None:
        self.disconnected = True
        self.mark()


def _clear_websocket_request_error_overrides(request_state: _WebSocketRequestState) -> None:
    request_state.error_code_override = None
    request_state.error_message_override = None
    request_state.error_type_override = None
    request_state.error_param_override = None
    request_state.failure_phase_override = None
    request_state.failure_detail_override = None
    request_state.upstream_error_code_override = None
    request_state.error_http_status_override = None


def _clear_websocket_precreated_replay_fallback(request_state: _WebSocketRequestState) -> None:
    if request_state.precreated_replay_reason != _ACCOUNT_MODEL_UNSUPPORTED_ERROR_CODE:
        return
    request_state.precreated_replay_reason = None
    request_state.precreated_replay_account_id = None
    _clear_websocket_request_error_overrides(request_state)


def _record_response_event(request_state: _WebSocketRequestState | None, event_type: str | None) -> None:
    if request_state is None or event_type is None or not event_type.startswith("response."):
        return
    if event_type in {"response.failed", "response.incomplete"}:
        return
    request_state.response_event_count += 1


def _websocket_request_can_replay_before_visible_output(request_state: _WebSocketRequestState) -> bool:
    if not request_state.request_text:
        return False
    if request_state.replay_count >= 1:
        return False
    sequenced_created_only_prewarm = (
        request_state.generate_false_prewarm
        and request_state.last_downstream_sequence_number == 0
        and request_state.response_id is not None
        and not request_state.awaiting_response_created
        and request_state.response_event_count == 1
        and not request_state.downstream_visible
    )
    if request_state.last_downstream_sequence_number is not None and not sequenced_created_only_prewarm:
        return False
    if request_state.downstream_visible:
        return False
    has_retry_safe_fresh_payload = (
        request_state.fresh_upstream_request_is_retry_safe and request_state.fresh_upstream_request_text is not None
    )
    precreated_pending = request_state.response_id is None and request_state.awaiting_response_created
    if precreated_pending and request_state.previous_response_id is not None and not has_retry_safe_fresh_payload:
        return False
    created_only_pending = (
        request_state.response_id is not None
        and not request_state.awaiting_response_created
        and request_state.response_event_count <= 1
        and (request_state.previous_response_id is None or has_retry_safe_fresh_payload)
    )
    if precreated_pending and request_state.response_event_count > 0:
        return False
    return precreated_pending or created_only_pending


def _record_websocket_route_metadata(
    request_state: _WebSocketRequestState,
    *,
    upstream: UpstreamResponsesWebSocket | None = None,
    route: ResolvedUpstreamRoute | None = None,
    fallback_used: bool | None = None,
) -> None:
    request_state.upstream_proxy_route_mode = getattr(upstream, "upstream_proxy_route_mode", None) or (
        route.mode if route is not None else None
    )
    request_state.upstream_proxy_pool_id = getattr(upstream, "upstream_proxy_pool_id", None) or (
        route.pool_id if route is not None else None
    )
    request_state.upstream_proxy_endpoint_id = getattr(upstream, "upstream_proxy_endpoint_id", None) or (
        route.endpoint_id if route is not None else None
    )
    upstream_fallback = getattr(upstream, "upstream_proxy_fallback_used", None)
    request_state.upstream_proxy_fallback_used = upstream_fallback if upstream_fallback is not None else fallback_used
    if request_state.upstream_proxy_endpoint_id is None:
        request_state.upstream_proxy_fallback_used = None
    request_state.upstream_proxy_fail_closed_reason = None


def _copy_websocket_route_metadata_to_session(
    session: _HTTPBridgeSession,
    request_state: _WebSocketRequestState,
) -> None:
    session.upstream_proxy_route_mode = request_state.upstream_proxy_route_mode
    session.upstream_proxy_pool_id = request_state.upstream_proxy_pool_id
    session.upstream_proxy_endpoint_id = request_state.upstream_proxy_endpoint_id
    session.upstream_proxy_fallback_used = request_state.upstream_proxy_fallback_used
    session.upstream_proxy_fail_closed_reason = request_state.upstream_proxy_fail_closed_reason


def _copy_websocket_route_metadata_from_session(
    request_state: _WebSocketRequestState,
    session: _HTTPBridgeSession,
) -> None:
    request_state.upstream_proxy_route_mode = session.upstream_proxy_route_mode
    request_state.upstream_proxy_pool_id = session.upstream_proxy_pool_id
    request_state.upstream_proxy_endpoint_id = session.upstream_proxy_endpoint_id
    request_state.upstream_proxy_fallback_used = session.upstream_proxy_fallback_used
    request_state.upstream_proxy_fail_closed_reason = session.upstream_proxy_fail_closed_reason


def _websocket_route_log_kwargs(request_state: _WebSocketRequestState) -> dict[str, str | bool | None]:
    return {
        "upstream_proxy_route_mode": request_state.upstream_proxy_route_mode,
        "upstream_proxy_pool_id": request_state.upstream_proxy_pool_id,
        "upstream_proxy_endpoint_id": request_state.upstream_proxy_endpoint_id,
        "upstream_proxy_fallback_used": (
            request_state.upstream_proxy_fallback_used if request_state.upstream_proxy_endpoint_id else None
        ),
        "upstream_proxy_fail_closed_reason": request_state.upstream_proxy_fail_closed_reason,
    }


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
    if isinstance(payload.get("error"), dict):
        return "error"
    return None


async def _wait_for_websocket_continuity_gap(
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        async with pending_lock:
            if not pending_requests:
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS, remaining))


async def _websocket_full_replay_should_wait_for_continuity(
    request_state: _WebSocketRequestState,
    pending_requests: deque[_WebSocketRequestState],
    *,
    pending_lock: anyio.Lock,
    codex_session_affinity: bool,
) -> bool:
    if (
        not codex_session_affinity
        or request_state.previous_response_id is not None
        or request_state.input_item_count < _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS
    ):
        return False
    async with pending_lock:
        return bool(pending_requests)


def _is_account_neutral_error_code(code: str | None) -> bool:
    return is_local_overload_error_code(code) or code in {
        PROCESS_NETWORK_UNAVAILABLE_CODE,
        "proxy_unavailable",
        "responses_compact_input_too_large",
    }


def _is_local_account_cap_code(code: str | None) -> bool:
    return code in {"account_response_create_cap", "account_stream_cap"}


def _http_error_status_from_payload(payload: dict[str, JsonValue] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    for status_field in ("status", "status_code"):
        status = payload.get(status_field)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
    return None


def _openai_error_envelope_from_response_failed_payload(
    payload: dict[str, JsonValue] | None,
) -> OpenAIErrorEnvelope:
    default_envelope = openai_error("upstream_error", "Upstream error")
    if not isinstance(payload, dict):
        return default_envelope
    response_payload = payload.get("response")
    if not isinstance(response_payload, dict):
        return default_envelope
    error_payload = response_payload.get("error")
    if not isinstance(error_payload, dict):
        return default_envelope

    message_value = error_payload.get("message")
    if isinstance(message_value, str) and message_value.strip():
        message = message_value.strip()
    else:
        message = "Upstream error"

    code_value = error_payload.get("code")
    code = code_value.strip() if isinstance(code_value, str) and code_value.strip() else "upstream_error"

    type_value = error_payload.get("type")
    error_type = type_value.strip() if isinstance(type_value, str) and type_value.strip() else "server_error"

    envelope = openai_error(code, message, error_type)
    param_value = error_payload.get("param")
    if isinstance(param_value, str) and param_value.strip():
        envelope["error"]["param"] = param_value.strip()
    error_detail = envelope["error"]
    plan_type = error_payload.get("plan_type")
    if plan_type is not None:
        error_detail["plan_type"] = str(plan_type)
    resets_at = error_payload.get("resets_at")
    if isinstance(resets_at, int | float):
        error_detail["resets_at"] = resets_at
    resets_in = error_payload.get("resets_in_seconds")
    if isinstance(resets_in, int | float):
        error_detail["resets_in_seconds"] = resets_in
    return envelope
