from __future__ import annotations

from typing import Literal, TypedDict


class UpstreamError(TypedDict, total=False):
    message: str
    resets_at: int | float
    resets_in_seconds: int | float


FailureClass = Literal["rate_limit", "quota", "retryable_transient", "non_retryable"]
FailurePhase = Literal["connect", "first_event", "mid_stream"]


class ClassifiedFailure(TypedDict):
    failure_class: FailureClass
    phase: FailurePhase
    error_code: str
    error: UpstreamError
    http_status: int | None
