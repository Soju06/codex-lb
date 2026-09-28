from __future__ import annotations

from typing import Any

import pytest

from app.core.balancer.logic import (
    BURST_SAME_ACCOUNT_BASE_SECONDS,
    BURST_SAME_ACCOUNT_MAX_RETRIES,
    BURST_SAME_ACCOUNT_MAX_WAIT_SECONDS,
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    ROUTING_POLICY_BURN_FIRST,
    AccountState,
    burst_same_account_backoff_seconds,
    evaluate_health_tier,
    failover_decision,
    handle_quota_exceeded,
    handle_rate_limit,
    select_account,
)
from app.core.balancer.types import ClassifiedFailure, FailureClass, UpstreamError
from app.db.models import AccountStatus
from app.modules.proxy.helpers import (
    _MESSAGE_CLASSIFIED_CODES,
    _QUOTA_CODES,
    _RATE_LIMIT_CODES,
    _TRANSIENT_CODES,
    _normalize_error_code,
    _parse_openai_error,
    _upstream_error_from_openai,
    classify_upstream_failure,
    is_message_derived_usage_limit_rejection,
    is_upstream_burst_rejection,
    is_upstream_model_capacity_error,
    is_upstream_usage_limit_rejection,
    keeps_account_in_the_walk,
)

pytestmark = pytest.mark.unit


# The exact envelope upstream answers a saturated account with, copied from the
# fixtures that already assert on it (``tests/unit/test_proxy_http_bridge.py``,
# ``tests/integration/test_proxy_websocket_responses.py``). The passive
# sentence is the wording that actually arrives; the ``type`` is what the
# code-less variant of the same rejection omits.
_OBSERVED_USAGE_LIMIT_ENVELOPE: dict[str, Any] = {
    "type": "error",
    "status": 429,
    "error": {
        "type": "usage_limit_reached",
        "message": "The usage limit has been reached",
        "plan_type": "team",
        "resets_at": 1_778_790_595,
        "resets_in_seconds": 14_555,
    },
}


def _classify_observed_envelope(envelope: dict[str, Any]) -> ClassifiedFailure:
    """Classify an upstream envelope the way the streaming path does.

    Going through the repository's own parse/normalize helpers keeps the test
    honest about what reaches ``classify_upstream_failure``: a test that builds
    the arguments by hand cannot notice that the marker table misses the
    wording upstream sends.
    """
    error = _parse_openai_error(envelope)
    return classify_upstream_failure(
        error_code=_normalize_error_code(
            error.code if error else None,
            error.type if error else None,
        ),
        error=_upstream_error_from_openai(error),
        http_status=envelope.get("status"),
        phase="first_event",
    )


# Every code the classifier has an opinion about, plus the two placeholders a
# missing or generic envelope normalizes to, plus one it has never heard of.
_ALL_CLASSIFIABLE_CODES = (
    _RATE_LIMIT_CODES | _QUOTA_CODES | _TRANSIENT_CODES | _MESSAGE_CLASSIFIED_CODES | {"invalid_request"}
)
# One message per behaviour the classifier reads out of a sentence, so the
# closure below crosses each with every code rather than sampling pairs.
_RETRY_HINT_CLOSURE_MESSAGES = (
    "The usage limit has been reached",
    "You've hit your usage limit.",
    "Selected model is at capacity. Please try a different model.",
    "Selected model is at capacity. The usage limit has been reached",
    "Rate limit exceeded",
    "",
)


def _was_stamped_before_the_message_branch(error_code: str, message: str) -> bool:
    """Whether a 429 of this shape carried the locally stamped ``Retry-After`` before.

    ``classify_upstream_failure`` without its usage-limit-message branch, then
    the burst test. Spelled out rather than imported because the point is to
    compare against the behaviour that no longer exists.
    """
    if error_code in _RATE_LIMIT_CODES or error_code in _QUOTA_CODES:
        return False
    return error_code in _TRANSIENT_CODES or is_upstream_model_capacity_error(message)


class TestClassifyUpstreamFailure:
    def test_rate_limit_exceeded(self) -> None:
        result = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=UpstreamError(message="Try again in 1.5s"),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "rate_limit"
        assert result["phase"] == "connect"

    def test_usage_limit_reached(self) -> None:
        result = classify_upstream_failure(
            error_code="usage_limit_reached",
            error=UpstreamError(message="Usage limit"),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "rate_limit"

    def test_insufficient_quota(self) -> None:
        result = classify_upstream_failure(
            error_code="insufficient_quota",
            error=UpstreamError(message="Quota exceeded"),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "quota"

    def test_quota_exceeded(self) -> None:
        result = classify_upstream_failure(
            error_code="quota_exceeded",
            error=UpstreamError(message=""),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "quota"

    def test_usage_not_included(self) -> None:
        result = classify_upstream_failure(
            error_code="usage_not_included",
            error=UpstreamError(message=""),
            http_status=403,
            phase="first_event",
        )
        assert result["failure_class"] == "quota"

    def test_server_error(self) -> None:
        result = classify_upstream_failure(
            error_code="server_error",
            error=UpstreamError(message="Internal error"),
            http_status=500,
            phase="mid_stream",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_http_500_unknown_code(self) -> None:
        result = classify_upstream_failure(
            error_code="unknown_thing",
            error=UpstreamError(message=""),
            http_status=500,
            phase="connect",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_http_502(self) -> None:
        result = classify_upstream_failure(
            error_code="bad_gateway",
            error=UpstreamError(message=""),
            http_status=502,
            phase="connect",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_overloaded_error(self) -> None:
        # Regression for #565: upstream "Our servers are currently overloaded.
        # Please try again later" is delivered with code=overloaded_error and
        # may surface without a 5xx status (e.g. on streamed responses where
        # the HTTP status was already 200 before the error envelope).
        # Classifying it as non_retryable made the agent stop mid-task instead
        # of failing over to another account or surfacing a retryable error.
        result = classify_upstream_failure(
            error_code="overloaded_error",
            error=UpstreamError(message="Our servers are currently overloaded. Please try again later"),
            http_status=None,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_server_is_overloaded(self) -> None:
        result = classify_upstream_failure(
            error_code="server_is_overloaded",
            error=UpstreamError(message="Our servers are currently overloaded. Please try again later"),
            http_status=None,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_selected_model_capacity_message(self) -> None:
        result = classify_upstream_failure(
            error_code="invalid_request_error",
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=400,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_generic_capacity_message_is_not_model_capacity_retry(self) -> None:
        result = classify_upstream_failure(
            error_code="invalid_request_error",
            error=UpstreamError(
                message=("This model has a fixed context capacity; reduce input size or try a different model."),
            ),
            http_status=400,
            phase="first_event",
        )
        assert result["failure_class"] == "non_retryable"

    def test_rate_limit_code_takes_precedence_over_capacity_message(self) -> None:
        result = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "rate_limit"

    def test_observed_usage_limit_envelope_keeps_its_coded_classification(self) -> None:
        result = _classify_observed_envelope(_OBSERVED_USAGE_LIMIT_ENVELOPE)

        assert result["error_code"] == "usage_limit_reached"
        assert result["failure_class"] == "rate_limit"

    def test_observed_usage_limit_envelope_without_a_code_is_still_a_usage_limit(self) -> None:
        # The same rejection, with the ``type`` upstream omits on the code-less
        # path: the passive sentence is then the only evidence of the limit.
        envelope = {
            **_OBSERVED_USAGE_LIMIT_ENVELOPE,
            "error": {key: value for key, value in _OBSERVED_USAGE_LIMIT_ENVELOPE["error"].items() if key != "type"},
        }

        result = _classify_observed_envelope(envelope)

        assert result["error_code"] == "upstream_error"
        assert result["failure_class"] == "rate_limit"
        assert result["excludes_account"] is True
        assert (
            is_upstream_burst_rejection(
                failure_class=result["failure_class"],
                http_status=result["http_status"],
            )
            is False
        )

    @pytest.mark.parametrize(
        ("error_code", "message", "expected_class"),
        [
            # Code-less: upstream's message is the only evidence of the limit.
            ("upstream_error", "You've hit your usage limit.", "rate_limit"),
            ("upstream_error", "Usage limit reached.", "rate_limit"),
            ("upstream_error", "The usage limit has been reached", "rate_limit"),
            ("upstream_error", "You have exceeded your usage limit.", "rate_limit"),
            # The envelope upstream rejects a request with, and a rejection
            # frequently quotes the request back: a phrase the client supplied
            # must not bench the account serving it. The classification stays
            # exactly what it was before the message branch existed.
            ("invalid_request_error", "You have reached your usage limit.", "non_retryable"),
            ("invalid_request_error", "The usage limit has been reached", "non_retryable"),
            # Coded envelopes keep the classification the code table gives them.
            ("rate_limit_exceeded", "You've hit your usage limit.", "rate_limit"),
            ("usage_limit_reached", "Usage limit reached.", "rate_limit"),
            ("insufficient_quota", "You've hit your usage limit.", "quota"),
            ("quota_exceeded", "Usage limit reached.", "quota"),
        ],
    )
    def test_usage_limit_message_truth_table(
        self,
        error_code: str,
        message: str,
        expected_class: FailureClass,
    ) -> None:
        result = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message=message),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == expected_class

    def test_usage_limit_message_without_http_status(self) -> None:
        # The serialized ``response.failed`` frame carries the same sentence
        # with no status at all, so the status cannot be part of the match.
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="You've hit your usage limit."),
            http_status=None,
            phase="mid_stream",
        )
        assert result["failure_class"] == "rate_limit"

    @pytest.mark.parametrize(
        "message",
        [
            # Wrapped mid-sentence: the words are only adjacent after folding.
            "You’ve hit your\nusage limit — try again later",
            # Hyphenated compound: a literal substring match never sees it.
            "You've hit your usage-limit.",
        ],
    )
    def test_usage_limit_message_matches_through_punctuation(self, message: str) -> None:
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message=message),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "rate_limit"

    @pytest.mark.parametrize(
        ("error_code", "http_status"),
        [
            # ``overloaded_error`` is retryable regardless of status, and a
            # transient code carries its own classification decision: neither
            # may be reversed by a message the envelope happens to repeat.
            ("overloaded_error", 429),
            ("overloaded_error", None),
            ("server_is_overloaded", 429),
            ("server_error", 500),
            ("stream_incomplete", None),
        ],
    )
    def test_transient_codes_keep_their_class_under_a_usage_limit_message(
        self,
        error_code: str,
        http_status: int | None,
    ) -> None:
        result = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message="The usage limit has been reached"),
            http_status=http_status,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_message_less_envelope_is_not_a_usage_limit(self) -> None:
        # A ``response.failed`` frame can arrive with an error object that has
        # no ``message`` at all; the match must not read one that is not there.
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"
        assert result["excludes_account"] is True

    def test_concurrency_limit_message_stays_transient(self) -> None:
        # A concurrency rejection says nothing about quota: it must stay in the
        # transient class so the burst backoff still applies to it.
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="Account stream concurrency limit reached"),
            http_status=429,
            phase="connect",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_non_retryable_bad_request(self) -> None:
        result = classify_upstream_failure(
            error_code="invalid_request",
            error=UpstreamError(message="Bad request"),
            http_status=400,
            phase="connect",
        )
        assert result["failure_class"] == "non_retryable"

    def test_non_retryable_auth(self) -> None:
        result = classify_upstream_failure(
            error_code="authentication_error",
            error=UpstreamError(message=""),
            http_status=401,
            phase="connect",
        )
        assert result["failure_class"] == "non_retryable"

    def test_preserves_error_payload(self) -> None:
        error: UpstreamError = {"message": "Try again", "resets_at": 1234567890}
        result = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=error,
            http_status=429,
            phase="connect",
        )
        assert result["error"] is error
        assert result["http_status"] == 429
        assert result["error_code"] == "rate_limit_exceeded"

    def test_stream_incomplete_is_transient(self) -> None:
        result = classify_upstream_failure(
            error_code="stream_incomplete",
            error=UpstreamError(message=""),
            http_status=None,
            phase="mid_stream",
        )
        assert result["failure_class"] == "retryable_transient"

    def test_upstream_error_is_transient(self) -> None:
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message=""),
            http_status=None,
            phase="connect",
        )
        assert result["failure_class"] == "retryable_transient"


class TestIsUpstreamBurstRejection:
    def test_truth_table(self) -> None:
        # Only a code-less HTTP 429 (classified retryable_transient) is a burst.
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=429) is True
        assert is_upstream_burst_rejection(failure_class="rate_limit", http_status=429) is False
        assert is_upstream_burst_rejection(failure_class="quota", http_status=429) is False
        assert is_upstream_burst_rejection(failure_class="non_retryable", http_status=429) is False
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=500) is False
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=503) is False
        assert is_upstream_burst_rejection(failure_class="retryable_transient", http_status=None) is False

    def test_composes_with_classify_for_the_prod_shape(self) -> None:
        # Prod: upstream 429 body carries only a message -> code normalizes to
        # ``upstream_error`` -> retryable_transient -> burst. Note that
        # ``classify_upstream_failure`` itself is unchanged: a bare
        # ``upstream_error`` with http_status=429 is still classified by the
        # transient code table, not by the status.
        codeless = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="Rate limit exceeded"),
            http_status=429,
            phase="connect",
        )
        assert codeless["failure_class"] == "retryable_transient"
        assert is_upstream_burst_rejection(failure_class=codeless["failure_class"], http_status=codeless["http_status"])
        coded = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=UpstreamError(message="Try again in 1.5s"),
            http_status=429,
            phase="connect",
        )
        assert coded["failure_class"] == "rate_limit"
        assert not is_upstream_burst_rejection(failure_class=coded["failure_class"], http_status=coded["http_status"])

    def test_usage_limit_message_429_is_not_a_burst(self) -> None:
        # Backing off 1 s / 2 s / 4 s on an account that is out of quota spends
        # the request budget on an account that cannot serve it.
        usage_limit = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="You've hit your usage limit."),
            http_status=429,
            phase="connect",
        )
        assert (
            is_upstream_burst_rejection(
                failure_class=usage_limit["failure_class"],
                http_status=usage_limit["http_status"],
            )
            is False
        )

    def test_model_capacity_message_429_is_a_burst(self) -> None:
        capacity = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=429,
            phase="connect",
        )
        assert (
            is_upstream_burst_rejection(
                failure_class=capacity["failure_class"],
                http_status=capacity["http_status"],
            )
            is True
        )


class TestClassifiedExcludesAccount:
    """``failure_class`` answers account health; ``excludes_account`` answers selection."""

    @pytest.mark.parametrize(
        "error_code",
        ["rate_limit_exceeded", "usage_limit_reached", "insufficient_quota", "quota_exceeded"],
    )
    def test_capacity_message_under_a_benching_code_still_excludes(self, error_code: str) -> None:
        """The health write is the authority on an account the walk may keep using.

        A capacity message suppresses exclusion only where the account survives
        the failure. Under a rate-limit or quota code it does not: the health
        write this classification selects persists a benched status, so
        answering "keep using this account" would contradict the write that
        just removed it from service.
        """
        result = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=429,
            phase="first_event",
        )

        state = AccountState("acct_capacity", AccountStatus.ACTIVE)
        if result["failure_class"] == "rate_limit":
            handle_rate_limit(state, result["error"])
        else:
            handle_quota_exceeded(state, result["error"])

        assert state.status is not AccountStatus.ACTIVE
        assert result["excludes_account"] is True

    def test_code_less_capacity_message_keeps_the_account_selectable(self) -> None:
        # No code benches this one, so its health write is the recoverable
        # transient penalty and the account is still usable. Rotating the pool
        # here would burn every account on a condition none of them can serve.
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"
        assert result["excludes_account"] is False

    @pytest.mark.parametrize(
        ("error_code", "message", "http_status"),
        [
            # Every walkable class excludes: the walk may move off all three.
            ("upstream_error", "You've hit your usage limit.", 429),
            ("rate_limit_exceeded", "Try again in 1.5s", 429),
            ("usage_limit_reached", "Usage limit reached.", 429),
            ("insufficient_quota", "Quota exceeded", 429),
            ("quota_exceeded", "", 429),
            # The code-less burst 429 the walk must move off rather than
            # surface, even though the account is not out of quota.
            ("upstream_error", "Rate limit exceeded", 429),
            ("server_error", "Internal error", 500),
            ("stream_incomplete", "", None),
            ("overloaded_error", "Our servers are currently overloaded.", None),
        ],
    )
    def test_walkable_failures_exclude_the_account(
        self,
        error_code: str,
        message: str,
        http_status: int | None,
    ) -> None:
        result = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message=message),
            http_status=http_status,
            phase="first_event",
        )
        assert result["excludes_account"] is True

    def test_usage_limit_message_outranks_a_capacity_match_on_a_transient_class(self) -> None:
        # ``overloaded_error`` keeps its own class, so the capacity carve-out is
        # live here and only the account-scoped usage limit overrides it. This
        # is the case that proves the precedence: under a benching code the
        # exclusion follows from the health write instead.
        result = classify_upstream_failure(
            error_code="overloaded_error",
            error=UpstreamError(message="Selected model is at capacity. The usage limit has been reached."),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "retryable_transient"
        assert result["excludes_account"] is True

    def test_usage_limit_message_outranks_a_capacity_match(self) -> None:
        result = classify_upstream_failure(
            error_code="rate_limit_exceeded",
            error=UpstreamError(message="Selected model is at capacity. The usage limit has been reached."),
            http_status=429,
            phase="first_event",
        )
        assert result["failure_class"] == "rate_limit"
        assert result["excludes_account"] is True

    def test_code_less_usage_limit_message_outranks_a_capacity_match(self) -> None:
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="Selected model is at capacity. You've hit your usage limit."),
            http_status=None,
            phase="first_event",
        )
        assert result["failure_class"] == "rate_limit"
        assert result["excludes_account"] is True

    @pytest.mark.parametrize(
        ("error_code", "message", "http_status"),
        [
            ("invalid_request", "Bad request", 400),
            ("authentication_error", "", 401),
            # The generic request-rejection envelope, carrying the usage-limit
            # phrase: never read from its message, so it keeps the class it had
            # and the walk ends on it instead of benching a serving account.
            ("invalid_request_error", "Usage limit reached.", 429),
        ],
    )
    def test_non_retryable_failures_do_not_exclude_the_account(
        self,
        error_code: str,
        message: str,
        http_status: int,
    ) -> None:
        # Nothing is walked away from a bad request; the walk ends instead.
        result = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message=message),
            http_status=http_status,
            phase="connect",
        )
        assert result["excludes_account"] is False


class TestMessageDerivedUsageLimitRejection:
    """A 429 reclassified by its message alone still owes the client a wait hint."""

    def test_message_derived_usage_limit_429_needs_the_surfaced_hint(self) -> None:
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="The usage limit has been reached"),
            http_status=429,
            phase="first_event",
        )
        assert is_message_derived_usage_limit_rejection(result) is True

    @pytest.mark.parametrize(
        ("error_code", "message", "http_status"),
        [
            # A coded rejection carries upstream reset metadata of its own.
            ("usage_limit_reached", "The usage limit has been reached", 429),
            ("rate_limit_exceeded", "Try again in 1.5s", 429),
            # Still a burst: it keeps the hint through the burst branch.
            ("upstream_error", "Rate limit exceeded", 429),
            # Not a 429, so no burst hint was ever stamped for it.
            ("upstream_error", "The usage limit has been reached", None),
        ],
    )
    def test_other_rejections_are_not_message_derived_usage_limits(
        self,
        error_code: str,
        message: str,
        http_status: int | None,
    ) -> None:
        result = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message=message),
            http_status=http_status,
            phase="first_event",
        )
        assert is_message_derived_usage_limit_rejection(result) is False

    @pytest.mark.parametrize("error_code", sorted(_ALL_CLASSIFIABLE_CODES))
    @pytest.mark.parametrize("message", _RETRY_HINT_CLOSURE_MESSAGES)
    def test_no_surfaced_429_loses_its_retry_hint(self, error_code: str, message: str) -> None:
        """Reclassification may add wait guidance to a 429; it may never take guidance away.

        The hint is stamped locally when the surfaced rejection would otherwise reach the client
        with nothing to wait on, and before the message branch existed the only 429s that got it
        were the ones classified transient. Rather than list the forms someone thought of, this
        walks every code the classifier knows crossed with every message that can move one, and
        asserts the new stamp set covers the old one.
        """
        classified = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message=message),
            http_status=429,
            phase="first_event",
        )
        stamped_now = is_upstream_burst_rejection(
            failure_class=classified["failure_class"],
            http_status=classified["http_status"],
        ) or is_message_derived_usage_limit_rejection(classified)

        assert stamped_now or not _was_stamped_before_the_message_branch(error_code, message)


class TestKeepsAccountInTheWalk:
    """What a transport asks before taking an account out of its own walk."""

    def test_a_capacity_rejection_on_a_walkable_class_keeps_the_account(self) -> None:
        result = classify_upstream_failure(
            error_code="server_error",
            error=UpstreamError(message="Selected model is at capacity. Please try a different model."),
            http_status=500,
            phase="first_event",
        )
        assert keeps_account_in_the_walk(result) is True

    @pytest.mark.parametrize(
        ("error_code", "message", "http_status"),
        [
            # The ordinary 500 the transient loop is built for.
            ("server_error", "Internal server error", 500),
            # A capacity message the usage limit outranks.
            ("server_error", "Selected model is at capacity. You've hit your usage limit.", 500),
            # A benching code: the health write already removed the account.
            ("usage_limit_reached", "Selected model is at capacity.", 429),
        ],
    )
    def test_other_walkable_rejections_release_the_account(
        self,
        error_code: str,
        message: str,
        http_status: int,
    ) -> None:
        result = classify_upstream_failure(
            error_code=error_code,
            error=UpstreamError(message=message),
            http_status=http_status,
            phase="first_event",
        )
        assert keeps_account_in_the_walk(result) is False

    def test_a_status_less_transport_failure_is_not_a_carve_out(self) -> None:
        """``non_retryable`` answers "the walk ends", never "keep this account".

        A transport failure carries no status and no transient code, so it
        classifies ``non_retryable`` and reports no exclusion -- but a transport
        that has stopped retrying it must still leave that account behind, or
        the next selection hands the request straight back to the dead route.
        """
        result = classify_upstream_failure(
            error_code="upstream_unavailable",
            error=UpstreamError(message="Server disconnected"),
            http_status=None,
            phase="first_event",
        )

        assert result["failure_class"] == "non_retryable"
        assert result["excludes_account"] is False
        assert keeps_account_in_the_walk(result) is False


class TestUsageLimitRejectionDrivingThePoolWalk:
    """What the walk asks before it treats a rejection as a spent subscription window.

    Named for the walk rather than for the predicate so that a second suite over
    the same predicate cannot shadow this one: Python keeps whichever class
    definition comes last in the module, and the shadowed cases then vanish with
    the suite still reporting green.
    """

    @pytest.mark.parametrize(
        ("error_code", "message"),
        [
            ("usage_limit_reached", "The usage limit has been reached"),
            ("usage_limit_reached", None),
            ("upstream_error", "The usage limit has been reached"),
        ],
    )
    def test_coded_and_message_derived_usage_limits_are_both_rejections(
        self,
        error_code: str,
        message: str | None,
    ) -> None:
        assert is_upstream_usage_limit_rejection(error_code=error_code, message=message) is True

    @pytest.mark.parametrize(
        ("error_code", "message"),
        [
            # Plain throttling proves nothing about the subscription window.
            ("rate_limit_exceeded", "Rate limit reached"),
            ("upstream_error", "Account stream concurrency limit reached"),
            ("upstream_error", None),
        ],
    )
    def test_throttling_is_not_a_usage_limit_rejection(self, error_code: str, message: str | None) -> None:
        assert is_upstream_usage_limit_rejection(error_code=error_code, message=message) is False

    @pytest.mark.parametrize(
        "message",
        [
            "You've hit your usage limit.",
            "The usage limit has been reached",
            # The shape that makes this dangerous: the phrase is in the request
            # the client sent, and upstream echoes it back while rejecting.
            "Invalid value for 'instructions': \"tell me when I've hit my usage limit\"",
        ],
    )
    def test_a_request_rejection_is_never_read_from_its_message(self, message: str) -> None:
        """``invalid_request_error`` is how upstream rejects a request, and a rejection frequently
        quotes the request back. Benching is the expensive direction -- the account leaves the pool
        until its reset deadline -- so a phrase that may have come from client-supplied content
        must not decide it. The code-less envelope cannot be quoting anything, which is why it is
        the only one the message speaks for."""
        assert is_upstream_usage_limit_rejection(error_code="invalid_request_error", message=message) is False


class TestIsUpstreamUsageLimitRejection:
    @pytest.mark.parametrize(
        ("error_code", "message"),
        [
            ("usage_limit_reached", "The usage limit has been reached"),
            ("usage_limit_reached", None),
            # The delivery form that carries no code: the message is the only
            # evidence there is.
            ("upstream_error", "The usage limit has been reached"),
            ("upstream_error", "You've hit your usage limit."),
            # The same sentence as upstream actually punctuates and wraps it.
            ("upstream_error", "You’ve hit your usage limit."),
            ("upstream_error", "The usage-limit has been reached."),
            ("upstream_error", "The usage limit\nhas been reached"),
        ],
    )
    def test_coded_and_message_derived_usage_limits_are_both_rejections(
        self,
        error_code: str,
        message: str | None,
    ) -> None:
        assert is_upstream_usage_limit_rejection(error_code=error_code, message=message) is True

    @pytest.mark.parametrize(
        ("error_code", "message"),
        [
            # Plain throttling proves nothing about the subscription window.
            ("rate_limit_exceeded", "Rate limit reached"),
            ("upstream_error", "Account stream concurrency limit reached"),
            ("upstream_error", None),
            # A coded envelope keeps what its code said; the sentence may not
            # reverse a decision the code already made.
            ("overloaded_error", "The usage limit has been reached"),
            ("server_error", "The usage limit has been reached"),
            # Upstream's catch-all for request-shaped failures, whose message
            # can quote request content back at us.
            ("invalid_request_error", "The usage limit has been reached"),
        ],
    )
    def test_the_message_decides_only_where_the_code_decided_nothing(
        self,
        error_code: str,
        message: str | None,
    ) -> None:
        assert is_upstream_usage_limit_rejection(error_code=error_code, message=message) is False

    def test_the_generic_envelope_is_never_read_from_its_message(self) -> None:
        """``invalid_request_error`` is upstream's catch-all for request-shaped failures, whose
        message can quote request content back. The paths that would ask about it forward their
        HTTP status to the health write as evidence only, so nothing downstream could tell a real
        rejection from an echo -- and a false bench is worse than the miss it would close."""
        assert (
            is_upstream_usage_limit_rejection(
                error_code="invalid_request_error", message="You've hit your usage limit."
            )
            is False
        )


class TestUsageLimitClassificationDeliveryForm:
    """Upstream picks the delivery form; the classification must not depend on which it picked."""

    @pytest.mark.parametrize("http_status", [429, None])
    def test_the_same_rejection_classifies_alike_with_and_without_a_status(self, http_status: int | None) -> None:
        # ``http_status=None`` is the serialized ``response.failed`` frame, which
        # carries no status at all; 429 is the HTTP body form of the same thing.
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="The usage limit has been reached"),
            http_status=http_status,
            phase="first_event",
        )

        assert result["failure_class"] == "rate_limit"

    def test_a_spent_account_is_not_filed_as_momentarily_busy(self) -> None:
        """``upstream_error`` is a transient code, so without the message this lands in the class
        whose remedy is waiting on the same account -- which an account with nothing left to give
        can never satisfy."""
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="The usage limit has been reached"),
            http_status=None,
            phase="first_event",
        )

        assert result["failure_class"] == "rate_limit"
        assert (
            is_upstream_burst_rejection(failure_class=result["failure_class"], http_status=result["http_status"])
            is False
        )

    def test_an_unrelated_status_less_frame_keeps_its_transient_class(self) -> None:
        result = classify_upstream_failure(
            error_code="upstream_error",
            error=UpstreamError(message="Upstream error"),
            http_status=None,
            phase="first_event",
        )

        assert result["failure_class"] == "retryable_transient"


class TestFailoverDecision:
    def test_surface_when_downstream_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=True,
                candidates_remaining=5,
            )
            == "surface"
        )

    def test_surface_when_no_candidates(self) -> None:
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=False,
                candidates_remaining=0,
            )
            == "surface"
        )

    def test_failover_rate_limit_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=False,
                candidates_remaining=2,
            )
            == "failover_next"
        )

    def test_failover_quota_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="quota",
                downstream_visible=False,
                candidates_remaining=1,
            )
            == "failover_next"
        )

    def test_failover_transient_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=1,
            )
            == "failover_next"
        )

    def test_surface_non_retryable_pre_visible(self) -> None:
        assert (
            failover_decision(
                failure_class="non_retryable",
                downstream_visible=False,
                candidates_remaining=5,
            )
            == "surface"
        )

    def test_visible_overrides_everything(self) -> None:
        for fc in ("rate_limit", "quota", "retryable_transient", "non_retryable"):
            assert (
                failover_decision(
                    failure_class=fc,
                    downstream_visible=True,
                    candidates_remaining=10,
                )
                == "surface"
            )


class TestEvaluateHealthTier:
    def _make_state(self, *, health_tier: int = 0, **kwargs) -> AccountState:
        defaults: dict = {
            "account_id": "test",
            "status": AccountStatus.ACTIVE,
            "health_tier": health_tier,
        }
        defaults.update(kwargs)
        return AccountState(**defaults)

    def test_healthy_stays_healthy_low_usage(self) -> None:
        state = self._make_state(used_percent=50.0, secondary_used_percent=60.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_healthy_to_draining_high_primary(self) -> None:
        state = self._make_state(used_percent=90.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_healthy_to_draining_high_secondary(self) -> None:
        state = self._make_state(secondary_used_percent=95.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_healthy_to_draining_error_spike(self) -> None:
        state = self._make_state(error_count=2, last_error_at=990.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_error_spike_outside_window_stays_healthy(self) -> None:
        state = self._make_state(error_count=2, last_error_at=900.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_draining_stays_draining_while_condition_holds(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=90.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=950.0) == HEALTH_TIER_DRAINING

    def test_draining_to_probing_after_quiet_period(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=50.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=930.0) == HEALTH_TIER_PROBING

    def test_draining_stays_if_quiet_period_not_elapsed(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=50.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=980.0) == HEALTH_TIER_DRAINING

    def test_probing_to_healthy_after_streak(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_PROBING)
        assert evaluate_health_tier(state, now=1000.0, probe_success_streak=3) == HEALTH_TIER_HEALTHY

    def test_probing_stays_probing_insufficient_streak(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_PROBING)
        assert evaluate_health_tier(state, now=1000.0, probe_success_streak=2) == HEALTH_TIER_PROBING

    def test_probing_to_draining_on_new_error(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_PROBING, error_count=2, last_error_at=990.0)
        assert evaluate_health_tier(state, now=1000.0, probe_success_streak=1) == HEALTH_TIER_DRAINING

    def test_hard_blocked_preserves_tier(self) -> None:
        for status in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED, AccountStatus.PAUSED):
            state = self._make_state(status=status, health_tier=HEALTH_TIER_DRAINING)
            assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_none_usage_stays_healthy(self) -> None:
        state = self._make_state()
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_draining_no_drain_entered_at_stays_draining(self) -> None:
        state = self._make_state(health_tier=HEALTH_TIER_DRAINING, used_percent=50.0)
        assert evaluate_health_tier(state, now=1000.0, drain_entered_at=None) == HEALTH_TIER_DRAINING

    def test_exactly_at_primary_threshold(self) -> None:
        state = self._make_state(used_percent=85.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING

    def test_just_below_primary_threshold(self) -> None:
        state = self._make_state(used_percent=84.9)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_HEALTHY

    def test_exactly_at_secondary_threshold(self) -> None:
        state = self._make_state(secondary_used_percent=90.0)
        assert evaluate_health_tier(state, now=1000.0) == HEALTH_TIER_DRAINING


class TestSelectAccountHealthTier:
    def test_prefers_healthy_over_draining(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=50.0, health_tier=HEALTH_TIER_DRAINING),
            AccountState("b", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_prefers_healthy_normal_over_draining_burn_first(self) -> None:
        states = [
            AccountState(
                "drain",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_DRAINING,
                routing_policy=ROUTING_POLICY_BURN_FIRST,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="fill_first")
        assert result.account is not None
        assert result.account.account_id == "healthy"

    def test_prefers_healthy_over_probing(self) -> None:
        states = [
            AccountState(
                "a",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=990.0,
            ),
            AccountState("b", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_due_probing_account_precedes_healthy(self) -> None:
        states = [
            AccountState(
                "probing",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=900.0,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]

        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")

        assert result.account is not None
        assert result.account.account_id == "probing"

    def test_never_selected_probing_account_is_due(self) -> None:
        states = [
            AccountState(
                "probing",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_PROBING,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_HEALTHY),
        ]

        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")

        assert result.account is not None
        assert result.account.account_id == "probing"

    def test_oldest_due_probing_account_is_deterministic(self) -> None:
        states = [
            AccountState(
                "probing-later",
                AccountStatus.ACTIVE,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=850.0,
            ),
            AccountState(
                "probing-tie-b",
                AccountStatus.ACTIVE,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=800.0,
            ),
            AccountState(
                "probing-tie-a",
                AccountStatus.ACTIVE,
                health_tier=HEALTH_TIER_PROBING,
                last_selected_at=800.0,
            ),
            AccountState("healthy", AccountStatus.ACTIVE, health_tier=HEALTH_TIER_HEALTHY),
        ]

        result = select_account(states, now=1000.0, routing_strategy="usage_weighted")

        assert result.account is not None
        assert result.account.account_id == "probing-tie-a"

    def test_prefers_probing_over_draining(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=10.0, health_tier=HEALTH_TIER_DRAINING),
            AccountState("b", AccountStatus.ACTIVE, used_percent=80.0, health_tier=HEALTH_TIER_PROBING),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_falls_back_to_draining_when_no_healthy(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=90.0, health_tier=HEALTH_TIER_DRAINING),
            AccountState("b", AccountStatus.ACTIVE, used_percent=50.0, health_tier=HEALTH_TIER_DRAINING),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_all_healthy_normal_selection(self) -> None:
        states = [
            AccountState("a", AccountStatus.ACTIVE, used_percent=50.0, health_tier=HEALTH_TIER_HEALTHY),
            AccountState("b", AccountStatus.ACTIVE, used_percent=10.0, health_tier=HEALTH_TIER_HEALTHY),
        ]
        result = select_account(states, routing_strategy="usage_weighted")
        assert result.account is not None
        assert result.account.account_id == "b"

    def test_capacity_weighted_respects_tier(self) -> None:
        states = [
            AccountState(
                "drain",
                AccountStatus.ACTIVE,
                used_percent=10.0,
                health_tier=HEALTH_TIER_DRAINING,
                plan_type="plus",
                capacity_credits=7560.0,
            ),
            AccountState(
                "healthy",
                AccountStatus.ACTIVE,
                used_percent=80.0,
                health_tier=HEALTH_TIER_HEALTHY,
                plan_type="plus",
                capacity_credits=7560.0,
            ),
        ]
        result = select_account(states, routing_strategy="capacity_weighted", deterministic_probe=True)
        assert result.account is not None
        assert result.account.account_id == "healthy"


class TestFailoverDecisionOwnerBound:
    """Owner-bound requests never fail over: retry the same account or surface."""

    @pytest.mark.parametrize("failure_class", ["rate_limit", "quota", "retryable_transient", "non_retryable"])
    def test_owner_bound_without_same_account_retry_surfaces(self, failure_class: FailureClass) -> None:
        assert (
            failover_decision(
                failure_class=failure_class,
                downstream_visible=False,
                candidates_remaining=5,
                owner_bound=True,
            )
            == "surface"
        )

    def test_owner_bound_with_same_account_retry_retries_same_account(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=0,
                owner_bound=True,
                same_account_retry_available=True,
            )
            == "retry_same_account"
        )

    def test_downstream_visible_overrides_owner_bound_retry(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=True,
                candidates_remaining=3,
                owner_bound=True,
                same_account_retry_available=True,
            )
            == "surface"
        )

    def test_same_account_retry_flag_is_ignored_when_not_owner_bound(self) -> None:
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=2,
                owner_bound=False,
                same_account_retry_available=True,
            )
            == "failover_next"
        )
        assert (
            failover_decision(
                failure_class="retryable_transient",
                downstream_visible=False,
                candidates_remaining=0,
                owner_bound=False,
                same_account_retry_available=True,
            )
            == "surface"
        )

    def test_defaults_keep_legacy_positional_free_callers(self) -> None:
        # websocket/mixin.py and compact.py call without the new keywords.
        assert (
            failover_decision(
                failure_class="rate_limit",
                downstream_visible=False,
                candidates_remaining=1,
            )
            == "failover_next"
        )


class TestBurstSameAccountBackoffSeconds:
    def test_exponential_schedule_without_retry_after(self) -> None:
        assert [
            burst_same_account_backoff_seconds(index, retry_after_seconds=None)
            for index in range(1, BURST_SAME_ACCOUNT_MAX_RETRIES + 1)
        ] == [1.0, 2.0, 4.0]
        assert BURST_SAME_ACCOUNT_BASE_SECONDS == 1.0

    def test_retry_after_is_a_floor_not_a_ceiling(self) -> None:
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=3) == 3.0
        assert burst_same_account_backoff_seconds(3, retry_after_seconds=3) == 4.0
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=0) == 1.0
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=-7) == 1.0

    def test_wait_is_capped(self) -> None:
        assert burst_same_account_backoff_seconds(1, retry_after_seconds=120) == BURST_SAME_ACCOUNT_MAX_WAIT_SECONDS
        assert burst_same_account_backoff_seconds(10, retry_after_seconds=None) == BURST_SAME_ACCOUNT_MAX_WAIT_SECONDS

    def test_retry_index_below_one_is_clamped(self) -> None:
        assert burst_same_account_backoff_seconds(0, retry_after_seconds=None) == 1.0
