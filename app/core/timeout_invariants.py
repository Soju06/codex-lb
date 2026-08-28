"""Startup timeout-invariant validation over raw ``Settings`` values.

This module intentionally validates only startup ``Settings`` fields and a
small set of code constants whose relations are fixed at import/runtime. It
does not validate per-request ContextVar overrides
(``app/core/clients/proxy.py:3450-3467``,
``app/modules/proxy/_service/streaming/helpers.py:861-868``,
``app/modules/proxy/_service/compact.py:727-738``,
``app/modules/proxy/_service/transcribe.py:230-232``,
``app/core/clients/files.py:77-90``, and
``app/modules/proxy/service.py:1464-1478``), runtime clamps/derived effective
values (``app/core/clients/proxy.py:1049-1088``,
``app/core/auth/refresh.py:391-395``, and
``app/modules/proxy/load_balancer.py:1846-1856``), or DB/API-key/model-source
runtime settings (``app/core/config/settings_cache.py:22-36``,
``app/modules/settings/api.py:547-710``,
``app/modules/proxy/_service/streaming/retry.py:153-165``, and
``app/modules/model_sources/forwarding.py:112-221``).
"""

from __future__ import annotations

import argparse
import logging
import operator
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class TimeoutSettings(Protocol):
    upstream_connect_timeout_seconds: float
    proxy_request_budget_seconds: float
    http_responses_stream_request_budget_seconds: float
    compact_request_budget_seconds: float
    sse_keepalive_interval_seconds: float
    http_responses_session_bridge_request_budget_seconds: float
    http_responses_session_bridge_stuck_gate_retire_after_seconds: float
    http_responses_session_bridge_clean_close_retry_jitter_max_seconds: float
    proxy_admission_wait_timeout_seconds: float
    proxy_account_lease_ttl_seconds: float
    model_registry_enabled: bool

    @property
    def model_registry_snapshot_max_age_seconds(self) -> int | float: ...

    timeout_invariant_validation_strict: bool


@dataclass(frozen=True, slots=True)
class TimeoutOperand:
    label: str
    evaluate: Callable[[TimeoutSettings], float]
    code_anchor: str


@dataclass(frozen=True, slots=True)
class TimeoutInvariantRule:
    id: str
    lhs: TimeoutOperand
    relation: str
    rhs: TimeoutOperand
    rationale: str


@dataclass(frozen=True, slots=True)
class TimeoutInvariantViolation:
    rule: TimeoutInvariantRule
    lhs_value: float
    rhs_value: float

    def format(self) -> str:
        return (
            f"{self.rule.id}: {self.rule.lhs.label}={self.lhs_value:g} "
            f"{self.rule.relation} {self.rule.rhs.label}={self.rhs_value:g} violated; "
            f"{self.rule.rationale} "
            f"(lhs: {self.rule.lhs.code_anchor}; rhs: {self.rule.rhs.code_anchor})"
        )


class TimeoutInvariantError(RuntimeError):
    def __init__(self, violations: Sequence[TimeoutInvariantViolation]) -> None:
        self.violations = tuple(violations)
        super().__init__("\n".join(violation.format() for violation in self.violations))


def _field(name: str, anchor: str) -> TimeoutOperand:
    return TimeoutOperand(name, lambda settings: float(getattr(settings, name)), anchor)


def _expr(label: str, anchor: str, evaluate: Callable[[TimeoutSettings], float]) -> TimeoutOperand:
    return TimeoutOperand(label, evaluate, anchor)


UPSTREAM_CONNECT = _field("upstream_connect_timeout_seconds", "app/core/clients/proxy.py:2720")
PROXY_BUDGET = _field("proxy_request_budget_seconds", "app/core/config/settings.py:260")
STREAM_BUDGET = _field(
    "http_responses_stream_request_budget_seconds",
    "app/modules/proxy/_service/streaming/helpers.py:724",
)
COMPACT_BUDGET = _field("compact_request_budget_seconds", "app/modules/proxy/_service/compact.py:585")
SSE_KEEPALIVE = _field("sse_keepalive_interval_seconds", "app/modules/proxy/api.py:3930")
TOKEN_REFRESH = _field("token_refresh_timeout_seconds", "app/modules/accounts/auth_manager.py:1123")
BRIDGE_BUDGET = _field(
    "http_responses_session_bridge_request_budget_seconds",
    "app/modules/proxy/_service/http_bridge/helpers.py:2469",
)
BRIDGE_CLEAN_CLOSE_JITTER = _field(
    "http_responses_session_bridge_clean_close_retry_jitter_max_seconds",
    "app/modules/proxy/_service/http_bridge/request_submit.py:294",
)
ADMISSION_WAIT = _field("proxy_admission_wait_timeout_seconds", "app/modules/proxy/service.py:768")
ACCOUNT_LEASE_TTL = _field("proxy_account_lease_ttl_seconds", "app/modules/proxy/load_balancer.py:1993")
BRIDGE_STUCK_GATE_HARD_ANCHOR_RETIRE = _expr(
    "2 * http_responses_session_bridge_stuck_gate_retire_after_seconds",
    "app/modules/proxy/_service/http_bridge/helpers.py:686",
    lambda settings: 2.0 * settings.http_responses_session_bridge_stuck_gate_retire_after_seconds,
)
MODEL_REGISTRY_SNAPSHOT_MAX_AGE = _field(
    "model_registry_snapshot_max_age_seconds",
    "app/core/openai/model_registry_store.py:367",
)
MODEL_REGISTRY_REFRESH_INTERVAL = _expr(
    "_REFRESH_INTERVAL_SECONDS",
    "app/core/openai/model_refresh_scheduler.py:37",
    lambda settings: _model_registry_refresh_interval_seconds(),
)
DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL = _expr(
    "DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS",
    "app/modules/proxy/durable_bridge_repository.py:42",
    lambda settings: _durable_bridge_retry_circuit_state_ttl_seconds(),
)
DURABLE_BRIDGE_RETRY_CIRCUIT_MIN_TTL = _expr(
    "_HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS + _HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS",
    "app/modules/proxy/_service/http_bridge/retry_circuit.py:19-21",
    lambda settings: _durable_bridge_retry_circuit_min_ttl_seconds(),
)


def _model_registry_refresh_interval_seconds() -> float:
    from app.core.openai.model_refresh_scheduler import _REFRESH_INTERVAL_SECONDS

    return float(_REFRESH_INTERVAL_SECONDS)


def _durable_bridge_retry_circuit_state_ttl_seconds() -> float:
    from app.modules.proxy.durable_bridge_repository import DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS

    return float(DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS)


def _durable_bridge_retry_circuit_min_ttl_seconds() -> float:
    from app.modules.proxy._service.http_bridge.retry_circuit import (
        _HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS,
        _HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS,
    )

    return float(_HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS + _HTTP_BRIDGE_RETRY_CIRCUIT_HALF_OPEN_LEASE_SECONDS)


TIMEOUT_INVARIANT_RULES: tuple[TimeoutInvariantRule, ...] = (
    TimeoutInvariantRule(
        "admission-wait-within-proxy-budget",
        ADMISSION_WAIT,
        "<=",
        PROXY_BUDGET,
        "Global admission waits must not consume more than the request budget they protect.",
    ),
    TimeoutInvariantRule(
        "admission-wait-within-stream-budget",
        ADMISSION_WAIT,
        "<=",
        STREAM_BUDGET,
        "Streaming retries wait for capacity inside the stream budget and must leave room for the stream attempt.",
    ),
    TimeoutInvariantRule(
        "admission-wait-within-compact-budget",
        ADMISSION_WAIT,
        "<=",
        COMPACT_BUDGET,
        "Compact response-create admission must not outlive the compact request budget.",
    ),
    TimeoutInvariantRule(
        "bridge-stuck-gate-retire-within-bridge-budget",
        BRIDGE_STUCK_GATE_HARD_ANCHOR_RETIRE,
        "<",
        BRIDGE_BUDGET,
        "Hard-continuity stuck gate retirement waits up to 2x the configured threshold and must happen before "
        "the bridge request budget is exhausted.",
    ),
    TimeoutInvariantRule(
        "account-lease-ttl-covers-proxy-budget",
        ACCOUNT_LEASE_TTL,
        ">=",
        PROXY_BUDGET,
        "Response-create leases use the raw lease TTL, so stale reclaim must not precede a healthy non-stream "
        "request deadline.",
    ),
    TimeoutInvariantRule(
        "account-lease-ttl-covers-compact-budget",
        ACCOUNT_LEASE_TTL,
        ">=",
        COMPACT_BUDGET,
        "Compact response-create leases must not be stale-reclaimed before the compact request budget expires.",
    ),
    TimeoutInvariantRule(
        "model-registry-snapshot-outlives-refresh-interval",
        MODEL_REGISTRY_SNAPSHOT_MAX_AGE,
        ">",
        MODEL_REGISTRY_REFRESH_INTERVAL,
        "Persisted model-registry snapshots must remain loadable for at least one fixed refresh cadence.",
    ),
    TimeoutInvariantRule(
        "durable-bridge-retry-circuit-ttl-covers-backoff-and-half-open",
        DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL,
        ">",
        DURABLE_BRIDGE_RETRY_CIRCUIT_MIN_TTL,
        "Durable HTTP bridge retry-circuit state must outlive the longest cooldown and half-open lease.",
    ),
)

# TODO(timeout_sem_001): database_migration_lock_timeout_seconds is independent startup DB migration policy.
# TODO(timeout_sem_008): proxy_downstream_websocket_idle_timeout_seconds has no verified ordering with bridge TTL.
# TODO(timeout_sem_009): oauth_timeout_seconds is used in OAuth/client flows, not a verified proxy-path deadline.
# TODO(timeout_sem_015): openai_cache_affinity_max_age_seconds participates with dashboard prompt-cache TTL in
# cleanup retention.
# TODO(timeout_sem_021): upstream_route_cache_ttl_seconds is invalidation freshness policy; no timeout inequality
# verified.
# timeout_sem_022 is enforced by model-registry-snapshot-outlives-refresh-interval.
# TODO(timeout_sem_023): firewall_ip_cache_ttl_seconds has no verified timeout owner beyond trust-cache freshness.
# TODO(timeout_sem_024): leader_election_ttl_seconds renewal is derived internally as ttl//3, not a cross-setting
# inequality.
# TODO(timeout_sem_027): proxy_account_cap_partition_scale_down_seconds is a stability window; exact heartbeat relation
# is internal.
# TODO(timeout_sem_029): usage_refresh_auth_failure_cooldown_seconds is policy cooldown, not a verified scheduler
# inequality.
# TODO(timeout_sem_030): shutdown_drain_timeout_seconds depends on deployment termination grace outside Settings.
# timeout_sem_031 is enforced by durable-bridge-retry-circuit-ttl-covers-backoff-and-half-open.
# TODO(timeout_sem_032/033): SQLite busy retry constants are module-local and not Settings-field rules.
# TODO(timeout_sem_034/035): account-selection recovery caps are module constants clamped by request deadlines at
# runtime.

_RELATIONS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def find_timeout_invariant_violations(settings: TimeoutSettings) -> list[TimeoutInvariantViolation]:
    violations: list[TimeoutInvariantViolation] = []
    for rule in TIMEOUT_INVARIANT_RULES:
        if rule.id == "model-registry-snapshot-outlives-refresh-interval" and not settings.model_registry_enabled:
            continue
        lhs_value = rule.lhs.evaluate(settings)
        rhs_value = rule.rhs.evaluate(settings)
        if not _RELATIONS[rule.relation](lhs_value, rhs_value):
            violations.append(TimeoutInvariantViolation(rule, lhs_value, rhs_value))
    return violations


def validate_timeout_invariants(
    settings: TimeoutSettings,
    *,
    strict: bool = False,
    log: bool = True,
) -> list[TimeoutInvariantViolation]:
    violations = find_timeout_invariant_violations(settings)
    if violations and log:
        for violation in violations:
            logger.critical("timeout invariant violation: %s", violation.format())
    if strict and violations:
        raise TimeoutInvariantError(violations)
    return violations


def validate_runtime_timeout_invariants(settings: TimeoutSettings) -> list[TimeoutInvariantViolation]:
    return validate_timeout_invariants(
        settings,
        strict=settings.timeout_invariant_validation_strict,
        log=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    from app.core.config.settings import get_settings

    parser = argparse.ArgumentParser(description="Validate codex-lb timeout invariants.")
    parser.add_argument("--strict", action="store_true", help="exit nonzero when any invariant is violated")
    args = parser.parse_args(argv)

    violations = validate_timeout_invariants(get_settings(), strict=False, log=True)
    if not violations:
        print(f"OK: {len(TIMEOUT_INVARIANT_RULES)} timeout invariant rules satisfied")
        return 0
    for violation in violations:
        print(violation.format(), file=sys.stderr)
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
