from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Iterable, cast

from pydantic import ValidationError

from app.core import usage as usage_core
from app.core.balancer.types import ClassifiedFailure, FailureClass, FailurePhase, UpstreamError
from app.core.errors import OpenAIErrorDetail, OpenAIErrorParam, is_upstream_usage_limit_message
from app.core.openai.chat_responses import _coerce_number
from app.core.openai.models import OpenAIError
from app.core.plan_types import normalize_rate_limit_plan_type
from app.core.types import JsonValue
from app.core.usage.types import UsageWindowRow, UsageWindowSummary
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.types import (
    CreditStatusDetailsData,
    RateLimitStatusDetailsData,
    RateLimitWindowSnapshotData,
)

PLAN_TYPE_PRIORITY = (
    "enterprise",
    "business",
    "team",
    "pro",
    "plus",
    "education",
    "edu",
    "free_workspace",
    "free",
    "go",
    "guest",
    "quorum",
    "k12",
)

_USAGE_LIMIT_CODE = "usage_limit_reached"
_RATE_LIMIT_CODES = frozenset({"rate_limit_exceeded", _USAGE_LIMIT_CODE})
_QUOTA_CODES = frozenset({"insufficient_quota", "usage_not_included", "quota_exceeded"})
_TRANSIENT_CODES = frozenset(
    {"server_error", "upstream_error", "stream_incomplete", "overloaded_error", "server_is_overloaded"}
)
_MODEL_CAPACITY_MESSAGE_MARKERS = ("selected model is at capacity",)
# The only two normalized codes that carry no classification decision of their
# own: ``upstream_error`` is what a missing code normalizes to, and
# ``invalid_request_error`` is the generic envelope type upstream reuses for
# rejections it has no code for. Every other code -- rate-limit, quota,
# ``overloaded_error``, any transient code -- already decided how the failure
# is classified, and a message must not reverse that decision.
_MESSAGE_CLASSIFIED_CODES = frozenset({"upstream_error", "invalid_request_error"})
# The classes whose account-health write benches the account outright: a
# persisted rate-limited or quota status with a reset deadline. Selection
# cannot use a benched account again in this request whatever its rejection
# said, so for these the health write -- not the message -- settles exclusion.
_BENCHING_FAILURE_CLASSES = frozenset({"rate_limit", "quota"})
_MODEL_UNSUPPORTED_MESSAGE_RE = re.compile(
    r"^The '.+' model is not supported when using Codex with a ChatGPT account\.$"
)


def is_model_scoped_upstream_rejection(message: str | None) -> bool:
    """Match the ChatGPT model-entitlement rejection for *any* requested model.

    The rejection names the model, not the account: it reproduces on every
    request for that model and says nothing about whether the serving account
    can still stream the models it is entitled to. Callers use it to keep the
    rejection out of account health while leaving failover alone -- a different
    account may hold a different entitlement.

    Unlike ``_is_account_model_unsupported_error`` this does not require the
    caller to know the requested model or the normalized error code. Upstream
    delivers this rejection over the Codex WebSocket with neither ``code`` nor
    ``type`` populated, which normalizes to the ``upstream_error`` fallback, so
    a code-gated match misses it on the live stream path.
    """
    if message is None:
        return False
    return _MODEL_UNSUPPORTED_MESSAGE_RE.fullmatch(" ".join(message.split())) is not None


def _is_account_model_unsupported_error(
    *,
    code: str | None,
    message: str | None,
    model: str | None,
) -> bool:
    """Match only the account-entitlement rejection for the requested model."""
    if code != "invalid_request_error" or message is None or model is None:
        return False
    normalized_message = " ".join(message.split())
    expected_message = f"The '{model}' model is not supported when using Codex with a ChatGPT account."
    return normalized_message == expected_message


def is_upstream_model_capacity_error(message: str | None) -> bool:
    if message is None:
        return False
    normalized_message = " ".join(message.lower().split())
    return any(marker in normalized_message for marker in _MODEL_CAPACITY_MESSAGE_MARKERS)


def is_upstream_usage_limit_rejection(*, error_code: str, message: str | None) -> bool:
    """True when upstream says this account's usage limit is spent.

    Either the code names the limit or the message does. The message-only form
    is how the code-less HTTP 429 and the serialized ``response.failed`` frame
    arrive, and it is the same rejection: callers that key on the literal code
    alone miss the majority of the traffic. Plain ``rate_limit_exceeded``
    throttling is deliberately not included -- it says the request arrived too
    fast, not that the subscription window is exhausted.
    """
    return error_code == _USAGE_LIMIT_CODE or is_upstream_usage_limit_message(message)


def classify_upstream_failure(
    *,
    error_code: str,
    error: UpstreamError,
    http_status: int | None,
    phase: FailurePhase,
) -> ClassifiedFailure:
    message = error.get("message")
    usage_limit_message = is_upstream_usage_limit_message(message)
    model_capacity_message = is_upstream_model_capacity_error(message)
    failure_class: FailureClass
    if error_code in _RATE_LIMIT_CODES:
        failure_class = "rate_limit"
    elif error_code in _QUOTA_CODES:
        failure_class = "quota"
    elif usage_limit_message and error_code in _MESSAGE_CLASSIFIED_CODES:
        # An account that is out of quota cannot be waited out on itself, so
        # this must not reach the transient branch that
        # ``is_upstream_burst_rejection`` reads as a burst and answers with
        # same-account backoff. Only the two codes that decided nothing are
        # raised this way; a coded envelope keeps the class its code chose.
        failure_class = "rate_limit"
    elif error_code in _TRANSIENT_CODES or model_capacity_message or (http_status is not None and http_status >= 500):
        failure_class = "retryable_transient"
    else:
        failure_class = "non_retryable"
    return ClassifiedFailure(
        failure_class=failure_class,
        phase=phase,
        error_code=error_code,
        error=error,
        http_status=http_status,
        # A capacity rejection describes the requested model, so moving off the
        # account for it would rotate the pool over a condition no account can
        # serve -- but that only holds while the account is still usable. A
        # coded rate-limit or quota rejection benches it, and telling selection
        # to keep an account the health write just took away is not a carve-out,
        # it is a contradiction. The usage limit is account-scoped and outranks
        # a capacity match on the class that does not bench. ``non_retryable``
        # never excludes: the walk ends on it rather than rotating the pool.
        excludes_account=(
            failure_class in _BENCHING_FAILURE_CLASSES
            or (failure_class == "retryable_transient" and (usage_limit_message or not model_capacity_message))
        ),
    )


def is_upstream_burst_rejection(*, failure_class: FailureClass, http_status: int | None) -> bool:
    """True for a code-less upstream HTTP 429: a per-account burst/concurrency
    rejection that ``classify_upstream_failure`` files as ``retryable_transient``
    (a 429 whose code or message proves a quota or usage limit lands in
    ``rate_limit`` / ``quota`` and is not a burst)."""
    return http_status == 429 and failure_class == "retryable_transient"


def is_message_derived_usage_limit_rejection(classified: ClassifiedFailure) -> bool:
    """True for an HTTP 429 whose usage limit was proven by its message alone.

    Read off the classification rather than re-matched: with a code that
    decided nothing, ``rate_limit`` on a 429 can only have come from the
    message. Such a rejection is what a burst rejection would otherwise have
    been, so it reaches the client with neither an upstream ``Retry-After`` nor
    an ``error.resets_at`` -- the locally stamped hint is the only wait
    guidance it can carry.
    """
    return (
        classified["http_status"] == 429
        and classified["failure_class"] == "rate_limit"
        and classified["error_code"] in _MESSAGE_CLASSIFIED_CODES
    )


def _header_account_id(account_id: str | None) -> str | None:
    if not account_id:
        return None
    if account_id.startswith(("email_", "local_")):
        return None
    return account_id


def _select_accounts_for_limits(accounts: Iterable[Account]) -> list[Account]:
    return [account for account in accounts if account.status not in (AccountStatus.DEACTIVATED, AccountStatus.PAUSED)]


def _summarize_window(
    rows: list[UsageWindowRow],
    account_map: dict[str, Account],
    window: str,
) -> UsageWindowSummary | None:
    if not rows:
        return None
    return usage_core.summarize_usage_window(rows, account_map, window)


def _window_snapshot(
    summary: UsageWindowSummary | None,
    rows: list[UsageWindowRow],
    window: str,
    now_epoch: int,
) -> RateLimitWindowSnapshotData | None:
    if summary is None:
        return None

    used_percent = _normalize_used_percent(summary.used_percent, rows)
    if used_percent is None:
        return None

    reset_at = summary.reset_at
    if reset_at is None:
        return None

    window_minutes = summary.window_minutes or usage_core.default_window_minutes(window)
    if not window_minutes:
        return None

    limit_window_seconds = int(window_minutes * 60)
    reset_after_seconds = max(0, int(reset_at) - now_epoch)

    return RateLimitWindowSnapshotData(
        used_percent=_percent_to_int(used_percent),
        limit_window_seconds=limit_window_seconds,
        reset_after_seconds=reset_after_seconds,
        reset_at=int(reset_at),
    )


def _normalize_used_percent(
    value: float | None,
    rows: Iterable[UsageWindowRow],
) -> float | None:
    if value is not None:
        return value
    values = [row.used_percent for row in rows if row.used_percent is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _percent_to_int(value: float) -> int:
    bounded = max(0.0, min(100.0, value))
    return int(bounded)


def _rate_limit_details(
    primary: RateLimitWindowSnapshotData | None,
    secondary: RateLimitWindowSnapshotData | None,
    monthly: RateLimitWindowSnapshotData | None = None,
    *,
    limit_reached: bool | None = None,
) -> RateLimitStatusDetailsData | None:
    if not primary and not secondary and not monthly:
        return None
    if limit_reached is None:
        used_percents = [window.used_percent for window in (primary, secondary, monthly) if window]
        limit_reached = any(used >= 100 for used in used_percents)
    return RateLimitStatusDetailsData(
        allowed=not limit_reached,
        limit_reached=limit_reached,
        primary_window=primary,
        secondary_window=secondary,
        monthly_window=monthly,
    )


def _aggregate_credits(entries: Iterable[UsageHistory]) -> tuple[bool, bool, float] | None:
    has_data = False
    has_credits = False
    unlimited = False
    balance_total = 0.0

    for entry in entries:
        credits_has = entry.credits_has
        credits_unlimited = entry.credits_unlimited
        credits_balance = entry.credits_balance
        if credits_has is None and credits_unlimited is None and credits_balance is None:
            continue
        has_data = True
        if credits_has is True:
            has_credits = True
        if credits_unlimited is True:
            unlimited = True
        if credits_balance is not None and not credits_unlimited:
            try:
                balance_total += float(credits_balance)
            except (TypeError, ValueError):
                continue

    if not has_data:
        return None
    if unlimited:
        has_credits = True
    return has_credits, unlimited, balance_total


def _credits_snapshot(entries: Iterable[UsageHistory]) -> CreditStatusDetailsData | None:
    aggregate = _aggregate_credits(entries)
    if aggregate is None:
        return None
    has_credits, unlimited, balance_total = aggregate
    balance_value = str(round(balance_total, 2))
    return CreditStatusDetailsData(
        has_credits=has_credits,
        unlimited=unlimited,
        balance=balance_value,
        approx_local_messages=None,
        approx_cloud_messages=None,
    )


def _plan_type_for_accounts(accounts: Iterable[Account]) -> str:
    normalized = [_normalize_plan_type(account.plan_type) for account in accounts]
    filtered = [plan for plan in normalized if plan is not None]
    if not filtered:
        return "guest"
    unique = set(filtered)
    if len(unique) == 1:
        return filtered[0]
    for plan in PLAN_TYPE_PRIORITY:
        if plan in unique:
            return plan
    return "guest"


def _normalize_plan_type(value: str | None) -> str | None:
    return normalize_rate_limit_plan_type(value)


def _rate_limit_headers(
    window_label: str,
    summary: UsageWindowSummary,
) -> dict[str, str]:
    used_percent = summary.used_percent
    window_minutes = summary.window_minutes
    if used_percent is None or window_minutes is None:
        return {}
    headers = {
        f"x-codex-{window_label}-used-percent": str(float(used_percent)),
        f"x-codex-{window_label}-window-minutes": str(int(window_minutes)),
    }
    reset_at = summary.reset_at
    if reset_at is not None:
        headers[f"x-codex-{window_label}-reset-at"] = str(int(reset_at))
    return headers


def _credits_headers(entries: Iterable[UsageHistory]) -> dict[str, str]:
    aggregate = _aggregate_credits(entries)
    if aggregate is None:
        return {}
    has_credits, unlimited, balance_total = aggregate
    balance_value = f"{balance_total:.2f}"
    return {
        "x-codex-credits-has-credits": "true" if has_credits else "false",
        "x-codex-credits-unlimited": "true" if unlimited else "false",
        "x-codex-credits-balance": balance_value,
    }


def _normalize_error_code(code: str | None, error_type: str | None) -> str:
    value = code or error_type
    if not value:
        return "upstream_error"
    return value.lower()


def _parse_openai_error(payload: Mapping[str, object]) -> OpenAIError | None:
    error = payload.get("error")
    if not isinstance(error, Mapping) or not error:
        return None
    error_mapping = cast(Mapping[str, JsonValue], error)
    param_state = OpenAIErrorParam.from_mapping(error_mapping)
    try:
        parsed = OpenAIError.model_validate(error_mapping)
    except ValidationError:
        parsed = OpenAIError(
            message=_coerce_str(error_mapping.get("message")),
            type=_coerce_str(error_mapping.get("type")),
            code=_coerce_str(error_mapping.get("code")),
            param=_coerce_str(error_mapping.get("param")),
            plan_type=_coerce_str(error_mapping.get("plan_type")),
            resets_at=_coerce_number(error_mapping.get("resets_at")),
            resets_in_seconds=_coerce_number(error_mapping.get("resets_in_seconds")),
        )
    parsed.set_param_state(param_state)
    return parsed


def _openai_error_param(error: OpenAIError | None) -> OpenAIErrorParam:
    return error.param_state if error is not None else OpenAIErrorParam.absent()


def _coerce_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _apply_error_metadata(target: OpenAIErrorDetail, error: OpenAIError | None) -> None:
    if not error:
        return
    if error.plan_type is not None:
        target["plan_type"] = error.plan_type
    if error.resets_at is not None:
        target["resets_at"] = error.resets_at
    if error.resets_in_seconds is not None:
        target["resets_in_seconds"] = error.resets_in_seconds


def _upstream_error_from_openai(error: OpenAIError | None) -> UpstreamError:
    if not error:
        return {}
    data = error.model_dump(exclude_none=True)
    payload: UpstreamError = {}
    message = data.get("message")
    if isinstance(message, str):
        payload["message"] = message
    resets_at = data.get("resets_at")
    if isinstance(resets_at, (int, float)):
        payload["resets_at"] = resets_at
    resets_in_seconds = data.get("resets_in_seconds")
    if isinstance(resets_in_seconds, (int, float)):
        payload["resets_in_seconds"] = resets_in_seconds
    return payload
