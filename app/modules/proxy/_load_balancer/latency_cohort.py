"""Combine the fleet-relative latency signals into one draw-weight multiplier.

Two replica-local signals describe how slow an account is relative to its
siblings: first-token latency per account (``ttft_cohort.py``) and output
throughput per (account, model) (``throughput_cohort.py``). Each yields a
multiplier in ``[floor, 1.0]``. This module applies their **minimum** to the
candidate's ``selection_weight_multiplier`` -- never their product: the two
signals measure the same defect (a slower serving path) from two angles, so an
account slow on both would otherwise be punished twice for one cause and drop
below the floor either signal promises.

Applied from ``load_balancer._build_states`` for the model the selection is
for; the weighted strategies multiply the candidate's weight by it alongside
the error-rate multiplier. Sticky owners, deterministic probes and
deterministic strategies never consult ``selection_weight_multiplier``.

Transition log: each signal has its own gate on the runtime, matching its
scope. The first-token multiplier is model-agnostic and gated once per account
(``RuntimeState.ttft_weight``), so one TTFT transition is one line no matter
how many models are requested; the throughput multiplier is gated per
(account, model) (``RuntimeState.tps_weights``), so alternating requests for a
model where the account is slow and one where it is not do not flap the log.
Only non-neutral throughput weights are stored: the map is keyed by the
requested model string, and dropping neutral entries keeps it bounded by the
models the account is actually discounted on (which requires in-window
evidence on that model) rather than by every model string a client sends.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

from app.core.balancer import AccountState
from app.modules.proxy._load_balancer.throughput_cohort import fleet_tps_estimates, tps_weight_multiplier
from app.modules.proxy._load_balancer.ttft_cohort import fleet_ttft_estimates, ttft_weight_multiplier
from app.modules.proxy._load_balancer.types import RuntimeState

logger = logging.getLogger(__name__)

# Transition log gate: a multiplier move smaller than this is not logged (a move of exactly this much is).
_LOG_DELTA = 0.1


def combine_cohort_multipliers(ttft_multiplier: float, tps_multiplier: float) -> tuple[float, str]:
    """``(multiplier, signal)``: the smaller of the two multipliers and which signal set it.

    ``signal`` is ``ttft``, ``tps``, ``both`` (equal and below neutral) or
    ``none`` (neutral). The multipliers are never compounded.
    """
    if ttft_multiplier >= 1.0 and tps_multiplier >= 1.0:
        return 1.0, "none"
    if ttft_multiplier < tps_multiplier:
        return ttft_multiplier, "ttft"
    if tps_multiplier < ttft_multiplier:
        return tps_multiplier, "tps"
    return ttft_multiplier, "both"


def last_tps_weight(runtime: RuntimeState, model: str | None) -> float:
    """The throughput multiplier last applied to ``runtime`` for ``model`` (``1.0`` when neutral or unknown)."""
    if runtime.tps_weights is None or not model:
        return 1.0
    return runtime.tps_weights.get(model, 1.0)


def last_cohort_weight(runtime: RuntimeState, model: str | None) -> float:
    """The combined multiplier the last builds applied to ``runtime`` for ``model`` (``1.0`` before any build)."""
    return min(runtime.ttft_weight, last_tps_weight(runtime, model))


def _transition(previous: float, current: float) -> bool:
    return (previous < 1.0) != (current < 1.0) or abs(current - previous) >= _LOG_DELTA


def _store_tps_weight(runtime: RuntimeState, model: str | None, multiplier: float) -> None:
    if not model:
        return  # a build without a model is always neutral on throughput
    if multiplier >= 1.0:
        if runtime.tps_weights is not None:
            runtime.tps_weights.pop(model, None)
            if not runtime.tps_weights:
                runtime.tps_weights = None
        return
    if runtime.tps_weights is None:
        runtime.tps_weights = {}
    runtime.tps_weights[model] = multiplier


def apply_latency_cohort_weights(
    states: Iterable[AccountState],
    runtime_by_account_id: Mapping[str, RuntimeState],
    *,
    now: float,
    model: str | None = None,
    log_transitions: bool = True,
) -> None:
    """Multiply each state's ``selection_weight_multiplier`` by its latency cohort weight for ``model``.

    Both fleet references are computed over the whole runtime map, not only the
    states being built, so a selection narrowed to a subset (API-key scoping,
    model catalog, continuity owner) is still weighed against the full pool.
    The throughput signal is neutral when ``model`` is ``None`` or when fewer
    than the minimum number of accounts hold evidence for that model; the
    first-token signal is model-agnostic. ``log_transitions=False`` is for
    builds on a detached runtime snapshot (observe-only admission): the
    multiplier is identical but the weight written there is discarded, so
    logging from it would repeat the transition on the next live build.
    """
    ttft_estimates, fleet_ttft_ms = fleet_ttft_estimates(runtime_by_account_id, now)
    tps_estimates, fleet_tps = fleet_tps_estimates(runtime_by_account_id, model, now)
    for state in states:
        runtime = runtime_by_account_id.get(state.account_id)
        if runtime is None:
            continue
        account_ttft_ms = ttft_estimates.get(state.account_id)
        account_tps = tps_estimates.get(state.account_id)
        ttft_multiplier = (
            1.0
            if fleet_ttft_ms is None or account_ttft_ms is None
            else ttft_weight_multiplier(account_ttft_ms, fleet_ttft_ms)
        )
        tps_multiplier = (
            1.0 if fleet_tps is None or account_tps is None else tps_weight_multiplier(account_tps, fleet_tps)
        )
        multiplier, signal = combine_cohort_multipliers(ttft_multiplier, tps_multiplier)
        if multiplier != 1.0:
            state.selection_weight_multiplier *= multiplier
        ttft_changed = _transition(runtime.ttft_weight, ttft_multiplier)
        tps_changed = _transition(last_tps_weight(runtime, model), tps_multiplier)
        if log_transitions and (ttft_changed or tps_changed):
            # Account identifiers are deliberately omitted: ``_build_states``
            # carries no redaction flag, so this line must stay identifier-free.
            logger.info(
                "latency_cohort_weight_change multiplier=%.2f signal=%s model=%s changed=%s ttft_multiplier=%.2f "
                "tps_multiplier=%.2f ttft_ms=%s fleet_ttft_ms=%s tps=%s fleet_tps=%s ttft_samples=%d "
                "tps_samples=%d accounts_with_ttft_evidence=%d accounts_with_tps_evidence=%d",
                multiplier,
                signal,
                model,
                "both" if ttft_changed and tps_changed else ("ttft" if ttft_changed else "tps"),
                ttft_multiplier,
                tps_multiplier,
                None if account_ttft_ms is None else round(account_ttft_ms),
                None if fleet_ttft_ms is None else round(fleet_ttft_ms),
                None if account_tps is None else round(account_tps, 1),
                None if fleet_tps is None else round(fleet_tps, 1),
                len(runtime.ttft_samples or ()),
                0 if not model else len((runtime.tps_samples or {}).get(model, ())),
                len(ttft_estimates),
                len(tps_estimates),
            )
        runtime.ttft_weight = ttft_multiplier
        _store_tps_weight(runtime, model, tps_multiplier)
