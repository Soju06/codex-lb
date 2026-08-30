from __future__ import annotations

import re
import time
from typing import Literal, NotRequired, TypedDict


class OpenAIErrorDetail(TypedDict, total=False):
    message: str
    type: str
    code: str
    param: str
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


def is_previous_response_not_found_error(
    *,
    code: str | None,
    param: object,
    message: str | None,
) -> bool:
    if code == PREVIOUS_RESPONSE_NOT_FOUND_CODE:
        return True
    if param is not None and not isinstance(param, str):
        return False
    if code != "invalid_request_error":
        return False
    if param is None:
        return _is_invalid_previous_response_id_message(message)
    if param != "previous_response_id":
        return False
    return is_previous_response_not_found_message(message) or _is_invalid_previous_response_id_message(message)


def response_failed_event(
    code: str,
    message: str,
    error_type: str = "server_error",
    response_id: str | None = None,
    created_at: int | None = None,
    error_param: str | None = None,
    resets_at: int | float | None = None,
    incomplete_details: dict[str, str] | None = None,
) -> ResponseFailedEvent:
    error = openai_error(code, message, error_type, resets_at=resets_at)["error"]
    if error_param is not None:
        error["param"] = error_param
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
