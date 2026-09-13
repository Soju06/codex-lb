"""Dashboard-managed routing weights and overload isolation (C2-2 routing/overload).

Five balancer knobs -- the overload isolation window, the error-rate weighting
kill switch, the in-flight pressure penalty, the leased-token weight and the
account lease TTL -- live in ``dashboard_settings`` with the process
environment as the fallback the dashboard inherits while its column is NULL
(``configuration-tiers``: code default < environment < dashboard).

Selection is a hot path that must not touch the database and must not read
settings while holding a runtime lock, so the values are resolved *once per
selection or lease operation* from the ``SettingsCache`` snapshot the proxy
service already holds for that stage (the same snapshot the concurrency caps
come from), frozen into a :class:`RoutingTunables`, and threaded through the
selection entry points next to ``routing_strategy`` and the caps; nothing is
re-read inside a lock section. Stages of one request share the cache's TTL
window. A caller without a snapshot (tests, tools) resolves from the
environment alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config.inheritable import resolve_inheritable
from app.modules.proxy._load_balancer.types import AccountLeaseKind

# Code defaults, kept in step with the ``Settings`` fields of the same name so
# a caller without any settings object still gets the shipped behaviour.
DEFAULT_OVERLOAD_ISOLATION_SECONDS = 1800
DEFAULT_ERROR_RATE_WEIGHTING_ENABLED = True
DEFAULT_INFLIGHT_PENALTY_PCT = 2.5
DEFAULT_LEASE_TOKEN_WEIGHT = 1.0
DEFAULT_LEASE_TTL_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class RoutingTunables:
    """Effective routing/overload knobs for one selection or lease operation."""

    overload_isolation_seconds: float = float(DEFAULT_OVERLOAD_ISOLATION_SECONDS)
    error_rate_weighting_enabled: bool = DEFAULT_ERROR_RATE_WEIGHTING_ENABLED
    inflight_penalty_pct: float = DEFAULT_INFLIGHT_PENALTY_PCT
    lease_token_weight: float = DEFAULT_LEASE_TOKEN_WEIGHT
    lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS


def resolve_routing_tunables(
    dashboard_settings: object | None,
    *,
    startup_settings: object,
) -> RoutingTunables:
    """Resolve the five knobs as code default < environment < dashboard.

    ``dashboard_settings`` is the cached ``dashboard_settings`` row (or ``None``
    when the caller has no snapshot); ``startup_settings`` is the process
    ``Settings``. Both are read with ``getattr`` so partial stand-ins used by
    tests resolve to the code default for the fields they omit.
    """

    def pick[T: (int, float, bool)](name: str, default: T) -> T:
        column = getattr(dashboard_settings, name, None)
        environment = getattr(startup_settings, name, None)
        return resolve_inheritable(column, environment, default).value

    return RoutingTunables(
        overload_isolation_seconds=float(pick("proxy_overload_isolation_seconds", DEFAULT_OVERLOAD_ISOLATION_SECONDS)),
        error_rate_weighting_enabled=bool(
            pick("proxy_account_error_rate_weighting_enabled", DEFAULT_ERROR_RATE_WEIGHTING_ENABLED)
        ),
        inflight_penalty_pct=float(pick("proxy_account_inflight_penalty_pct", DEFAULT_INFLIGHT_PENALTY_PCT)),
        lease_token_weight=float(pick("proxy_account_lease_token_weight", DEFAULT_LEASE_TOKEN_WEIGHT)),
        lease_ttl_seconds=float(pick("proxy_account_lease_ttl_seconds", DEFAULT_LEASE_TTL_SECONDS)),
    )


_ACCOUNT_STREAM_LEASE_STALE_GRACE_SECONDS = 60.0


def account_lease_stale_ttl_seconds(
    kind: AccountLeaseKind, settings: object, *, routing_tunables: RoutingTunables
) -> float:
    """Age after which a lease of ``kind`` is reclaimed as stale.

    The lease TTL comes from the dashboard snapshot; a stream lease also
    outlives the effective request budgets (``settings`` carries the C2-1
    dashboard overlay) plus a grace period.
    """
    ttl_seconds = float(routing_tunables.lease_ttl_seconds)
    if kind != "stream":
        return ttl_seconds
    valid_stream_budget_seconds = max(
        ttl_seconds,
        float(getattr(settings, "proxy_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_stream_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_session_bridge_request_budget_seconds", ttl_seconds)),
    )
    return max(ttl_seconds, valid_stream_budget_seconds + _ACCOUNT_STREAM_LEASE_STALE_GRACE_SECONDS)
