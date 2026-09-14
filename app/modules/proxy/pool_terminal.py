"""Client-visible failure for an account walk that ended without a served response.

A walk ends holding one rejection per attempted account and no response. None of
those rejections is by itself the pool's answer -- each belongs to the account
that produced it -- so the answer is decided from two things: which bound ended
the walk, and, when that bound leaves the question open, what the selector says
about the accounts that are left.

Two bounds answer themselves and never reach the selector:

* ``non_retryable`` -- a failure no account can route around belongs to the
  request, not to the fleet. It is surfaced as itself; rendering an exhausted
  pool over it would tell the client to wait for a reset that would not help;
* ``deadline`` -- a request that ran out of budget mid-walk is a timeout, and
  "Budget exhaustion during a walk ends the walk" requires
  ``upstream_request_timeout`` rather than a usage-limit rejection, whatever the
  pool's state happens to be.

For the bounds that do leave the question open -- an exhausted candidate list,
the runaway ceiling, a selector that stopped making progress -- the terminal
point asks ``probe_pool_usage_exhaustion`` once, with the same eligibility
filtering ordinary selection applies, and renders one of two answers:

* the pool is usage-exhausted -> the canonical ``usage_limit_reached`` 429
  required by "Pool usage exhaustion is reported as a usage-limit error",
  carrying ``error.resets_at`` when selection holds an authoritative reset
  deadline and never a ``Retry-After`` header: that requirement makes the reset
  deadline the whole retry hint;
* anything else, including the decline the probe returns under the drain routing
  strategies -> the preserved failure of the last attempted account, handed back
  as the very object the transport caught, so drain strategies and
  ``single_account`` keep byte-identical responses.

The probe is reused rather than reimplemented: it is the one place pool
exhaustion is decided, and its observe-only contract -- no lease, no health
refresh, no persisted write -- is what makes it safe to ask at a point where the
request is already failing.

Every ending is logged with the bound that caused it, because an operator
reading a failed request needs to tell a bad request from an exhausted fleet
from a request that simply ran out of time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.balancer import PoolWalkBound
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.utils.request_id import ensure_request_id
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._load_balancer.exhaustion_probe import AdmissionProbeService, probe_pool_usage_exhaustion
from app.modules.proxy.selection_errors import selection_failure_response

logger = logging.getLogger(__name__)

# The status, code and wording every other pre-visible budget check in the proxy
# raises, so a walk that ends on the deadline is recognised as the same
# exhaustion by ``_is_proxy_budget_exhausted_error`` and rendered by the same
# ``response.failed`` path.
BUDGET_EXHAUSTED_STATUS = 502
BUDGET_EXHAUSTED_CODE = "upstream_request_timeout"
BUDGET_EXHAUSTED_MESSAGE = "Proxy request budget exhausted"

# Bounds whose ending is the pool's to explain, and which therefore consult it.
_BOUNDS_THAT_ASK_THE_POOL: frozenset[PoolWalkBound] = frozenset({"pool_exhausted", "ceiling", "no_progress"})


@dataclass(frozen=True, slots=True)
class PoolUsageLimit:
    """The pool's own rejection, built by the selection-failure renderer the API layer already speaks."""

    status_code: int
    payload: OpenAIErrorEnvelope
    resets_at: int | None

    @property
    def message(self) -> str:
        """Read back from the envelope so a request log or SSE frame cannot drift from the body."""

        return self.payload["error"]["message"]


# The pool's rejection or the account's, never a synthesized third answer.
PoolTerminalFailure = PoolUsageLimit | ProxyResponseError


def _budget_exhausted() -> ProxyResponseError:
    return ProxyResponseError(
        BUDGET_EXHAUSTED_STATUS,
        openai_error(BUDGET_EXHAUSTED_CODE, BUDGET_EXHAUSTED_MESSAGE),
    )


def _record(ended_by: PoolWalkBound, answer: str, *, model: str | None, service_tier: str | None) -> None:
    logger.info(
        "pool_walk_terminal request_id=%s ended_by=%s answer=%s model=%s service_tier=%s",
        ensure_request_id(),
        ended_by,
        answer,
        model,
        service_tier,
    )


async def resolve_pool_terminal_failure(
    service: AdmissionProbeService,
    *,
    settings: object,
    api_key: ApiKeyData | None,
    model: str | None,
    service_tier: str | None,
    last_account_failure: ProxyResponseError | None,
    ended_by: PoolWalkBound,
) -> PoolTerminalFailure | None:
    """Decide what the client sees when a walk ends without a served response.

    ``ended_by`` is the bound that ended the walk. It is required because the
    endings are not interchangeable: only some of them may be answered with the
    pool's own usage-limit rejection, and the rest carry their own answer.

    ``last_account_failure`` is the last attempted account's rejection, preserved
    by the transport exactly as it was caught. It is ``None`` when the walk ended
    before any account rejected the request (selection itself found nothing), and
    a pool that is not exhausted then leaves the caller on its own
    selection-failure path.

    ``settings`` is the dashboard-settings snapshot the request routes with, the
    same object the probe reads ``routing_strategy`` from.
    """

    if ended_by == "deadline":
        _record(ended_by, "budget_exhausted", model=model, service_tier=service_tier)
        return _budget_exhausted()
    if ended_by not in _BOUNDS_THAT_ASK_THE_POOL:
        _record(ended_by, "account_failure", model=model, service_tier=service_tier)
        return last_account_failure
    try:
        exhaustion = await probe_pool_usage_exhaustion(
            service,
            settings=settings,
            api_key=api_key,
            model=model,
            service_tier=service_tier,
        )
    except Exception:
        # A walk that reached here already holds a client-visible failure. A
        # probe that cannot answer must leave that failure alone instead of
        # upgrading a routing outcome into an internal error.
        logger.warning(
            "pool_terminal_probe_failed request_id=%s model=%s service_tier=%s",
            ensure_request_id(),
            model,
            service_tier,
            exc_info=True,
        )
        _record(ended_by, "account_failure", model=model, service_tier=service_tier)
        return last_account_failure
    if exhaustion is None:
        _record(ended_by, "account_failure", model=model, service_tier=service_tier)
        return last_account_failure
    status_code, payload = selection_failure_response(exhaustion.selection)
    _record(ended_by, "pool_usage_limit", model=model, service_tier=service_tier)
    return PoolUsageLimit(status_code=status_code, payload=payload, resets_at=exhaustion.resets_at)
