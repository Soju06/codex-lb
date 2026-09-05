from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from app.core.types import JsonValue


@dataclass(frozen=True, slots=True)
class OpenAIErrorParam:
    """Preserve whether an upstream error supplied a ``param`` field."""

    present: bool
    raw: JsonValue | None = None

    @classmethod
    def absent(cls) -> "OpenAIErrorParam":
        return cls(False, None)

    @classmethod
    def from_mapping(cls, error: Mapping[str, JsonValue]) -> "OpenAIErrorParam":
        if "param" not in error:
            return cls.absent()
        return cls(True, error["param"])

    @property
    def normalized(self) -> str | None:
        return self.raw.strip() if isinstance(self.raw, str) else None

    @property
    def malformed(self) -> bool:
        return self.present and (self.normalized is None or not self.normalized)


def coerce_error_param(param: OpenAIErrorParam | JsonValue) -> OpenAIErrorParam:
    """Convert legacy raw values to presence-aware error parameter state."""

    if isinstance(param, OpenAIErrorParam):
        return param
    if param is None:
        return OpenAIErrorParam.absent()
    return OpenAIErrorParam(True, param)


def normalize_public_error_param(param: OpenAIErrorParam | JsonValue) -> str | None:
    """Return only a trimmed, non-empty string safe for a public response."""

    normalized = coerce_error_param(param).normalized
    return normalized if normalized else None


class OpenAIErrorDetail(TypedDict, total=False):
    message: str
    type: str
    code: str
    param: JsonValue
    plan_type: str
    resets_at: int | float
    resets_in_seconds: int | float


class OpenAIErrorEnvelope(TypedDict):
    error: OpenAIErrorDetail


class DashboardErrorDetail(TypedDict):
    code: str
    message: str


class DashboardErrorEnvelope(TypedDict):
    error: DashboardErrorDetail


class ResponseFailedResponse(TypedDict):
    object: str
    status: str
    error: OpenAIErrorDetail
    id: NotRequired[str]
    created_at: NotRequired[int]
    incomplete_details: NotRequired[dict[str, str] | None]


class ResponseFailedEvent(TypedDict):
    type: Literal["response.failed"]
    response: ResponseFailedResponse
    _codex_lb_synthetic_transport_failure: NotRequired[bool]


PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE = "Upstream websocket closed before response.completed"
# Local bridge recovery (fresh replay, context-overflow rollover, previous
# response rebind) tears down our own upstream session. It is not an upstream
# close and must not be reported as one.
HTTP_BRIDGE_LOCAL_RESET_MESSAGE = "HTTP responses session bridge reset this session locally before response.completed"
# ``stream_incomplete`` raised against a continuation anchor is ambiguous: the
# turn may already exist upstream. Neither of these messages proves the account
# misbehaved, so neither may drive an account-health penalty.
STREAM_INCOMPLETE_ANCHOR_NEUTRAL_MESSAGES = frozenset(
    {
        PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
        HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
    }
)
# Pre-response-start bridge silence. Distinct from ``stream_idle_timeout``,
# whose budget (``stream_idle_timeout_seconds``) only governs gaps *after*
# ``response.created``. When the bridge saw no unmatched upstream liveness,
# nothing was created upstream and a retry forks no context. If liveness was
# observed but not matched to a response, callers must treat retry as
# at-least-once.
HTTP_BRIDGE_EVENTLESS_TIMEOUT_CODE = "bridge_eventless_timeout"
PREVIOUS_RESPONSE_NOT_FOUND_CODE = "previous_response_not_found"
PREVIOUS_RESPONSE_NOT_FOUND_MESSAGE = "Previous response was not found; retry without previous_response_id."
PREVIOUS_RESPONSE_MALFORMED_PARAM_REASON = "previous_response_not_found_malformed_param"
SYNTHETIC_TRANSPORT_FAILURE_MARKER = "_codex_lb_synthetic_transport_failure"
SYNTHETIC_TRANSPORT_FAILURE_CODES = frozenset(
    {"stream_incomplete", "stream_idle_timeout", "upstream_request_timeout", "upstream_unavailable"}
)


def openai_error(
    code: str,
    message: str,
    error_type: str = "server_error",
    *,
    resets_at: int | float | None = None,
) -> OpenAIErrorEnvelope:
    detail: OpenAIErrorDetail = {"message": message, "type": error_type, "code": code}
    if resets_at is not None:
        detail["resets_at"] = int(resets_at)
    return {"error": detail}


def dashboard_error(code: str, message: str) -> DashboardErrorEnvelope:
    return {"error": {"code": code, "message": message}}


def previous_response_stream_incomplete_error() -> OpenAIErrorEnvelope:
    return openai_error(
        "stream_incomplete",
        PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
        error_type="server_error",
    )


def is_previous_response_not_found_message(message: str | None) -> bool:
    if message is None:
        return False
    normalized = " ".join(message.lower().split())
    return "previous response" in normalized and "not found" in normalized


def _is_invalid_previous_response_id_message(message: str | None) -> bool:
    if message is None:
        return False
    normalized = " ".join(message.casefold().replace("`", "").split()).removesuffix(".").rstrip()
    return normalized == "invalid previous_response_id"


def previous_response_id_from_not_found_message(message: str | None) -> str | None:
    if message is None:
        return None
    normalized = " ".join(message.split())
    match = re.search(
        r"""previous\s+response\s+with\s+id\s+['"](?P<response_id>[^'"]+)['"]\s+not\s+found""",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    response_id = match.group("response_id").strip()
    return response_id or None


def sanitize_public_error_detail(error: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Copy an error detail while omitting malformed ``param`` values."""

    normalized = dict(error)
    if "param" not in normalized:
        return normalized
    public_param = normalize_public_error_param(OpenAIErrorParam.from_mapping(error))
    if public_param is None:
        normalized.pop("param", None)
    else:
        normalized["param"] = public_param
    return normalized


def is_previous_response_not_found_error(
    *,
    code: str | None,
    param: OpenAIErrorParam | JsonValue,
    message: str | None,
) -> bool:
    param_state = coerce_error_param(param)
    if param_state.malformed:
        return False
    if code == PREVIOUS_RESPONSE_NOT_FOUND_CODE:
        return True
    if code != "invalid_request_error":
        return False
    if not param_state.present:
        return _is_invalid_previous_response_id_message(message)
    if param_state.normalized != "previous_response_id":
        return False
    return is_previous_response_not_found_message(message) or _is_invalid_previous_response_id_message(message)


def is_previous_response_not_found_public_shape(
    *,
    code: str | None,
    param: OpenAIErrorParam | JsonValue,
    message: str | None,
) -> bool:
    """Match stale-anchor errors for masking without authorizing replay."""

    if code == PREVIOUS_RESPONSE_NOT_FOUND_CODE:
        return True
    param_state = coerce_error_param(param)
    if (
        code == "invalid_request_error"
        and param_state.present
        and not param_state.malformed
        and param_state.normalized != "previous_response_id"
    ):
        return False
    if code == "invalid_request_error" and (
        _is_invalid_previous_response_id_message(message)
        or previous_response_id_from_not_found_message(message) is not None
    ):
        return True
    return is_previous_response_not_found_error(code=code, param=param, message=message)


def response_failed_event(
    code: str,
    message: str,
    error_type: str = "server_error",
    response_id: str | None = None,
    created_at: int | None = None,
    error_param: OpenAIErrorParam | JsonValue = None,
    resets_at: int | float | None = None,
    incomplete_details: dict[str, str] | None = None,
) -> ResponseFailedEvent:
    error = openai_error(code, message, error_type, resets_at=resets_at)["error"]
    public_param = normalize_public_error_param(error_param)
    if public_param is not None:
        error["param"] = public_param
    if created_at is None:
        created_at = int(time.time())
    response: ResponseFailedResponse = {
        "object": "response",
        "status": "failed",
        "error": error,
    }
    response["incomplete_details"] = incomplete_details
    if response_id:
        response["id"] = response_id
    if created_at is not None:
        response["created_at"] = created_at
    return {"type": "response.failed", "response": response}


def synthetic_transport_failure_event(event: ResponseFailedEvent) -> ResponseFailedEvent:
    """Mark an LB-generated transport terminal for boundary-only handling."""
    event[SYNTHETIC_TRANSPORT_FAILURE_MARKER] = True
    return event


def synthetic_stream_failure_event(
    code: str,
    message: str,
    error_type: str = "server_error",
    response_id: str | None = None,
    created_at: int | None = None,
    error_param: OpenAIErrorParam | JsonValue = None,
    resets_at: int | float | None = None,
    incomplete_details: dict[str, str] | None = None,
) -> ResponseFailedEvent:
    """Build a failed event and mark transport codes manufactured by codex-lb."""
    event = response_failed_event(
        code,
        message,
        error_type,
        response_id,
        created_at,
        error_param,
        resets_at,
        incomplete_details,
    )
    return synthetic_transport_failure_event(event) if code in SYNTHETIC_TRANSPORT_FAILURE_CODES else event
