"""Read-only pool-exhaustion probe (#2123 WP-C1, design v3 §4.3).

The overflow trigger is exactly the spec's 429 predicate: the pool is exhausted
iff the real selector, asked the same question the request would ask (same
model, same ``service_tier``, same API-key account scope, ``lease_kind=None`` so
local account caps do not apply), answers ``usage_limit_reached``. Every other
answer — an admitted account, a closed burn window, ``no_accounts``, a plan or
quota gate — is "not exhausted" and leaves today's behaviour to the caller.

The probe touches no account state (I4): it asks for an ``observe_only``
check, which builds the selector's states from a detached snapshot of the
balancer runtime, so it acquires no lease, refreshes no health tier, bumps no
runtime version and writes no sticky or persisted state. It returns the
selector's own ``AccountSelection`` so the caller can rebuild today's structured
429 (message and ``resets_at``) verbatim.

Parity between this observation and foreground selection is established only
outside the drain strategies (design decision 28): under ``sequential_drain``,
``reset_drain`` and ``single_account`` the opportunistic and foreground
selectors draw from different budget subsets and return the subset's answer
directly, so the probe declines there — it reports "not exhausted" without
consulting the selector, counts the decline and logs the reason. Drain
strategies never trigger overflow in v1.

Takes a Protocol rather than ``ProxyService`` to stay import-cycle free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.core.balancer import USAGE_LIMIT_REACHED
from app.core.metrics.prometheus import pool_exhaustion_probe_declined_total
from app.modules.api_keys.service import ApiKeyData

if TYPE_CHECKING:
    from app.modules.proxy._load_balancer.types import AccountLeaseKind
    from app.modules.proxy.load_balancer import AccountSelection

logger = logging.getLogger(__name__)

# Strategies for which ``_select_account_preferring_budget_safe`` returns the
# budget subset's answer directly; the opportunistic (secondary threshold
# applied) and foreground (not applied) subsets differ, so probe/foreground
# parity is not established and the probe must not claim exhaustion.
DRAIN_ROUTING_STRATEGIES: frozenset[str] = frozenset({"sequential_drain", "reset_drain", "single_account"})
# ``reason`` label of ``codex_lb_pool_exhaustion_probe_declined_total`` (closed set).
PROBE_DECLINED_DRAIN_STRATEGY = "drain_strategy"


@dataclass(frozen=True, slots=True)
class PoolExhaustion:
    """The selector's own ``usage_limit_reached`` answer, retained so the caller can rebuild today's 429."""

    resets_at: int | None
    selection: AccountSelection


class AdmissionProbeService(Protocol):
    async def check_opportunistic_admission(
        self,
        *,
        api_key: ApiKeyData | None,
        model: str | None,
        service_tier: str | None,
        lease_kind: AccountLeaseKind | None,
        observe_only: bool,
    ) -> AccountSelection: ...


def _inc(counter: Any, **labels: str) -> None:
    if counter is None:
        return
    try:
        counter.labels(**labels).inc()
    except Exception:  # metrics never break the request path
        logger.debug("pool_exhaustion_probe_metric_failed labels=%s", labels, exc_info=True)


async def probe_pool_usage_exhaustion(
    service: AdmissionProbeService,
    *,
    settings: object,
    api_key: ApiKeyData | None,
    model: str | None,
    service_tier: str | None,
) -> PoolExhaustion | None:
    """``PoolExhaustion`` iff ``selection.error_code == USAGE_LIMIT_REACHED``; ``None`` for every other answer.

    ``settings`` is the dashboard-settings snapshot the caller routes with
    (the same object ``opportunistic_admission_account_scope`` reads); its
    ``routing_strategy`` decides the drain-strategy decline before any
    selection work is done.
    """

    routing_strategy = getattr(settings, "routing_strategy", None)
    if isinstance(routing_strategy, str) and routing_strategy in DRAIN_ROUTING_STRATEGIES:
        _inc(pool_exhaustion_probe_declined_total, reason=PROBE_DECLINED_DRAIN_STRATEGY)
        logger.info(
            "pool_exhaustion_probe_declined reason=%s routing_strategy=%s model=%s service_tier=%s key_id=%s",
            PROBE_DECLINED_DRAIN_STRATEGY,
            routing_strategy,
            model,
            service_tier,
            api_key.id if api_key is not None else None,
        )
        return None
    selection = await service.check_opportunistic_admission(
        api_key=api_key,
        model=model,
        service_tier=service_tier,
        lease_kind=None,
        observe_only=True,
    )
    if selection.error_code != USAGE_LIMIT_REACHED:
        return None
    return PoolExhaustion(resets_at=selection.resets_at, selection=selection)
