"""Dashboard-managed resilience toggles (soft drain, deterministic failover, circuit breaker).

Each toggle is a nullable ``dashboard_settings`` column that inherits the
``CODEX_LB_*`` environment variable (deprecated alias) and then the code
default, resolved through :func:`resolve_inheritable`. Hot paths resolve the
toggles from the dashboard-settings snapshot they already hold; they never read
the database or ``get_settings().<toggle>`` directly.

The upstream client is reached from many request paths without a settings
argument, so the request path that resolved the toggles binds them to the
current task with :func:`bind_resilience_toggles` (same pattern as the
per-request timeout overrides in ``app.core.clients.proxy``) and the client
reads them back with :func:`current_resilience_toggles`. An unbound task falls
back to the environment layer, which is exactly the pre-dashboard behaviour.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Final

from app.core.config.inheritable import resolve_inheritable
from app.core.config.settings import Settings, get_settings

# ``dashboard_settings`` column, ``Settings`` field and provenance key share
# one name (configuration-tiers).
RESILIENCE_TOGGLE_SETTINGS: Final[tuple[str, ...]] = (
    "soft_drain_enabled",
    "deterministic_failover_enabled",
    "circuit_breaker_enabled",
)


@dataclass(frozen=True, slots=True)
class ResilienceToggles:
    soft_drain_enabled: bool
    deterministic_failover_enabled: bool
    circuit_breaker_enabled: bool


def _resolve_toggle(dashboard_settings: object | None, startup_settings: object, name: str) -> bool:
    default = bool(Settings.model_fields[name].default)
    return bool(
        resolve_inheritable(
            getattr(dashboard_settings, name, None),
            bool(getattr(startup_settings, name, default)),
            default,
        ).value
    )


def resolve_resilience_toggles(
    dashboard_settings: object | None,
    *,
    startup_settings: object | None = None,
) -> ResilienceToggles:
    """Resolve the three toggles from a dashboard-settings snapshot.

    ``dashboard_settings`` is the cached ``DashboardSettings`` row (or any
    object exposing the same attributes; missing attributes read as NULL).
    ``startup_settings`` supplies the environment layer when a caller already
    holds it; it defaults to ``get_settings()``.
    """
    startup = get_settings() if startup_settings is None else startup_settings
    return ResilienceToggles(
        soft_drain_enabled=_resolve_toggle(dashboard_settings, startup, "soft_drain_enabled"),
        deterministic_failover_enabled=_resolve_toggle(dashboard_settings, startup, "deterministic_failover_enabled"),
        circuit_breaker_enabled=_resolve_toggle(dashboard_settings, startup, "circuit_breaker_enabled"),
    )


_CURRENT_RESILIENCE_TOGGLES: contextvars.ContextVar[ResilienceToggles | None] = contextvars.ContextVar(
    "resilience_toggles",
    default=None,
)


def bind_resilience_toggles(
    dashboard_settings: object | None,
    *,
    startup_settings: object | None = None,
) -> ResilienceToggles:
    """Resolve the toggles from ``dashboard_settings`` and bind them to the current task.

    Request paths call this right after taking their dashboard-settings
    snapshot so the upstream client (which has no settings argument) gates
    circuit-breaker use on the same snapshot. The binding lives in the task's
    context; a later call in the same task (for example a fresh upstream
    WebSocket connect) rebinds it.
    """
    toggles = resolve_resilience_toggles(dashboard_settings, startup_settings=startup_settings)
    _CURRENT_RESILIENCE_TOGGLES.set(toggles)
    return toggles


def set_resilience_toggles(toggles: ResilienceToggles) -> ResilienceToggles:
    """Bind already-resolved toggles to the current task.

    ContextVars follow tasks, not generators: a streaming generator whose first
    item is produced by one task (startup probe) and whose upstream attempt runs
    in another must rebind, in the attempting task, the toggles its request
    path resolved.
    """
    _CURRENT_RESILIENCE_TOGGLES.set(toggles)
    return toggles


def current_resilience_toggles(*, startup_settings: object | None = None) -> ResilienceToggles:
    """Return the toggles bound to the current task, or the environment layer when unbound."""
    bound = _CURRENT_RESILIENCE_TOGGLES.get()
    if bound is not None:
        return bound
    return resolve_resilience_toggles(None, startup_settings=startup_settings)
