from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from app.core.types import JsonValue


@dataclass(frozen=True, slots=True)
class OpenAIErrorParam:
    """The wire-level state of an OpenAI error ``param`` field.

    ``None`` is a valid JSON value, so it cannot by itself distinguish a
    missing field from an explicitly supplied JSON ``null``.  Keep presence
    beside the raw value while normalizers and classifiers are moving an
    upstream error through the proxy.  The raw value is never a public error
    field; callers must deliberately choose a safe string before constructing
    a response for a client.
    """

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
        """Return a trimmed string, or ``None`` for a non-string value."""

        return self.raw.strip() if isinstance(self.raw, str) else None

    @property
    def malformed(self) -> bool:
        """Whether a present value cannot be trusted as a parameter name."""

        return self.present and (self.normalized is None or not self.normalized)


def normalize_public_error_param(param: OpenAIErrorParam | JsonValue) -> str | None:
    """Return the only ``param`` representation safe for a client response.

    Error parsing and recovery classification use :class:`OpenAIErrorParam`
    so an explicitly supplied malformed value remains distinct from an
    omitted field.  Public serializers must not expose that raw wire value;
    they may emit only a non-empty, trimmed string.
    """

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


PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE = "Upstream websocket closed before response.completed"
PREVIOUS_RESPONSE_NOT_FOUND_CODE = "previous_response_not_found"
PREVIOUS_RESPONSE_NOT_FOUND_MESSAGE = "Previous response was not found; retry without previous_response_id."
# Continuity fail-closed reason for a canonical stale-anchor error whose ``param``
# is present but malformed. Masked on the public surface, never recovered.
PREVIOUS_RESPONSE_MALFORMED_PARAM_REASON = "previous_response_not_found_malformed_param"


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


def coerce_error_param(param: OpenAIErrorParam | JsonValue) -> OpenAIErrorParam:
    """Normalize legacy raw values and presence-aware wire state.

    A raw ``None`` is the legacy shorthand for an omitted field. Callers that
    parsed a mapping and need to preserve an explicit JSON ``null`` must pass
    ``OpenAIErrorParam(True, None)`` (normally via :meth:`from_mapping`).
    """

    if isinstance(param, OpenAIErrorParam):
        return param
    if param is None:
        return OpenAIErrorParam.absent()
    return OpenAIErrorParam(True, param)


def sanitize_public_error_detail(error: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Copy an OpenAI error detail with a client-safe ``param`` field.

    This is intentionally a boundary helper.  Callers that still need to
    classify or replay an upstream event must retain the original mapping and
    its presence-aware :class:`OpenAIErrorParam` state instead of using this
    sanitized copy.
    """

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
    # A present blank, null, or non-string parameter is not equivalent to an
    # omitted parameter.  Preserve the raw JSON value while failing closed.
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
    """Public-surface masking test for stale-anchor upstream errors.

    This is deliberately a superset of :func:`is_previous_response_not_found_error`.
    The recovery classifier fails closed on a present-but-malformed ``param`` so
    the proxy never replays a turn on untrustworthy metadata; masking must not
    inherit that, or a canonical ``previous_response_not_found`` carrying a
    malformed ``param`` would be forwarded verbatim and leak both the internal
    classifier code and the raw malformed value to a public ``/v1`` client.

    Masking therefore keys off the canonical code or a clearly stale-anchor
    message, even when a non-canonical error carries malformed parameter
    metadata. Non-canonical shapes still use the strict recovery classifier for
    replay authorization without changing how their parameter metadata is
    interpreted.
    """
    if code == PREVIOUS_RESPONSE_NOT_FOUND_CODE:
        return True
    if code == "invalid_request_error" and (
        is_previous_response_not_found_message(message) or _is_invalid_previous_response_id_message(message)
    ):
        # A malformed ``param`` must not let a message that contains a raw
        # previous-response id pass through an unmasked public boundary.
        return True
    return is_previous_response_not_found_error(
        code=code,
        param=param,
        message=message,
    )


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
    # ``response.failed`` is only ever built for a client, so the raw wire
    # state stops here: callers keep their presence-aware ``OpenAIErrorParam``
    # for classification and replay authorization, while the serialized event
    # carries a trimmed non-empty string or no ``param`` at all.
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
