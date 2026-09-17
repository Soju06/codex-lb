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
    # The selection predicate, not an exhaustion one: may the request stop
    # using this account and reselect? True for every class a walk may move away
    # from -- ``rate_limit``, ``quota`` and ``retryable_transient`` alike, which
    # includes the code-less burst 429 the walk must not surface while a sibling
    # remains. False for ``non_retryable``, where the walk ends instead, and for
    # a model-capacity rejection, which describes the requested model rather
    # than the account and would otherwise rotate the whole pool over a
    # condition no account can serve.
    #
    # The capacity carve-out stops where the health write benches the account.
    # A rejection classified ``rate_limit`` or ``quota`` persists a benched
    # status and a reset deadline whatever its message says, so its health write
    # is the authority on the account's fate and this field agrees with it: it
    # stays true there even under a capacity message, because answering "keep
    # using this account" would hand selection an account health just took away.
    # It is only on ``retryable_transient`` -- the class whose write is a
    # recoverable error penalty -- that a capacity message suppresses exclusion,
    # and an account-scoped usage-limit message outranks the capacity match even
    # there. ``failure_class`` keeps answering the separate question of how the
    # account's health is recorded, and account health must keep reading it.
    excludes_account: bool
