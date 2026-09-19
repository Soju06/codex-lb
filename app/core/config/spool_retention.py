"""Dashboard-managed retention of the durable HTTP-bridge operation spool (R2, slop-removal 0908).

``http_responses_session_bridge_operation_spool_retention_seconds`` is a
nullable ``dashboard_settings`` column that inherits the deprecated
``CODEX_LB_*`` environment alias and then the code default (7 days), resolved
through :func:`resolve_inheritable`. Its home is the dashboard's data retention
card because the spool holds raw request payloads: a shorter window deletes
prompt material sooner.

The window has a floor. The spool is the replay source for durable bridge
recovery, so it must outlive every window in which something may still read a
spooled operation (:func:`operation_spool_retention_floor_seconds`). Purging
earlier deletes the transcript out from under a live reader -- which is why
abandoned durable bridge rows are already retained for the session reuse window
(``responses-api-compat`` "Retain completed recovery transcripts").

Neither consumer is on a request path: startup runs one bounded purge and the
leader-gated cleanup scheduler runs one pass per tick, each resolving the value
once per pass from the dashboard snapshot it already holds -- never inside a
runtime lock and never per operation.
"""

from __future__ import annotations

import logging
from typing import Final

from app.core.config.inheritable import resolve_inheritable
from app.core.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# ``dashboard_settings`` column, ``Settings`` field and provenance key share
# one name (configuration-tiers).
OPERATION_SPOOL_RETENTION_SETTING: Final = "http_responses_session_bridge_operation_spool_retention_seconds"

# NOT NULL ``dashboard_settings`` columns; no environment layer.
_DASHBOARD_REUSE_WINDOW_COLUMNS: Final[tuple[str, ...]] = (
    "openai_cache_affinity_max_age_seconds",
    "http_responses_session_bridge_prompt_cache_idle_ttl_seconds",
)
_BRIDGE_REQUEST_BUDGET_SETTING: Final = "http_responses_session_bridge_request_budget_seconds"

# Settings whose value moves the floor, so a PUT touching any of them is
# re-checked against it.
SPOOL_RETENTION_FLOOR_INPUTS: Final[tuple[str, ...]] = (
    *_DASHBOARD_REUSE_WINDOW_COLUMNS,
    _BRIDGE_REQUEST_BUDGET_SETTING,
)

_OPERATION_SPOOL_RETENTION_DEFAULT: Final[float] = float(
    Settings.model_fields[OPERATION_SPOOL_RETENTION_SETTING].default
)
_BRIDGE_REQUEST_BUDGET_DEFAULT: Final[float] = float(Settings.model_fields[_BRIDGE_REQUEST_BUDGET_SETTING].default)
# Floor of the stale-operation abandonment sweep's inactivity window
# (``session_registry.abandon_stale_http_bridge_operations``).
_STALE_OPERATION_ABANDONMENT_FLOOR_SECONDS: Final[float] = 30.0 * 60.0
# An ever-claimed retry circuit gets one extra TTL of grace in both
# ``retry_circuit`` and ``purge_retry_circuits_before``, because a claim leaves
# ``updated_at_epoch`` untouched.
_CLAIMED_RETRY_CIRCUIT_TTL_MULTIPLIER: Final[float] = 2.0


def _effective_seconds(dashboard_settings: object | None, name: str, environment_value: float, default: float) -> float:
    """One inheritable duration from either snapshot shape.

    Accepts the ``DashboardSettings`` row (a NULL column inherits) and the
    ``DashboardSettingsData`` the settings API builds (already effective, so
    the value resolves to itself).
    """
    column_value = getattr(dashboard_settings, name, None)
    return float(
        resolve_inheritable(None if column_value is None else float(column_value), environment_value, default).value
    )


def bridge_session_reuse_window_seconds(dashboard_settings: object) -> float:
    """Longest window in which an idle durable bridge session stays reusable.

    An idle local bridge session stays reusable until its effective idle TTL --
    up to the prompt-cache reuse TTL for prompt-cache sessions -- which can
    exceed the prompt-cache affinity max age. The two idle TTLs are fixed
    constants (#2256) read through their module at call time, so a test
    monkeypatching the attribute is honoured here too; the import is lazy
    because this module is configuration and that module is the request-path
    implementation owning them.

    Both column terms are NOT NULL on ``dashboard_settings``, so a real row
    always carries them; a term is skipped only for the partial snapshot fakes
    the startup and scheduler tests build, the same tolerance
    ``warn_environment_shadowed_by_dashboard`` and ``effective_settings`` apply
    to that row.
    """
    from app.modules.proxy._service.http_bridge import helpers as _http_bridge_helpers

    terms = [
        float(_http_bridge_helpers.HTTP_BRIDGE_IDLE_TTL_SECONDS),
        float(_http_bridge_helpers.HTTP_BRIDGE_CODEX_IDLE_TTL_SECONDS),
    ]
    for name in _DASHBOARD_REUSE_WINDOW_COLUMNS:
        value = getattr(dashboard_settings, name, None)
        if value is not None:
            terms.append(float(value))
    return max(terms)


def spool_retention_floor_terms_seconds(
    dashboard_settings: object,
    *,
    startup_settings: object | None = None,
) -> dict[str, float]:
    """Every window that may still read a spooled operation, by term name.

    - ``bridge_session_reuse_window``: while the owning session is still
      reusable, the next request on it resolves the operation ledger and may
      replay a spooled transcript.
    - ``stale_operation_abandonment_window``: until the abandonment sweep
      fences an ownerless nonterminal operation, that operation is still
      reading and appending its own spool.
    - ``claimed_retry_circuit_lifetime``: a surviving retry circuit can admit a
      replay of an operation whose spool must therefore still exist. A claim
      advances only the admission generation and leaves the row timestamp
      alone, so an ever-claimed circuit is honoured -- by both the loader and
      the scheduled purge -- for two TTLs, not one.

    The terms are read from the configuration in force, so this is a
    steady-state bound. A live bridge session keeps the idle TTL it captured
    (``session_registry`` only ever max-promotes it), so lowering a reuse
    window does not shorten sessions that are already open; the abandoned-row
    retention derived from the same windows is non-retroactive in exactly the
    same way.
    """
    from app.modules.proxy.durable_bridge_repository import DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS

    startup = get_settings() if startup_settings is None else startup_settings
    bridge_request_budget = _effective_seconds(
        dashboard_settings,
        _BRIDGE_REQUEST_BUDGET_SETTING,
        float(getattr(startup, _BRIDGE_REQUEST_BUDGET_SETTING, _BRIDGE_REQUEST_BUDGET_DEFAULT)),
        _BRIDGE_REQUEST_BUDGET_DEFAULT,
    )
    return {
        "bridge_session_reuse_window": bridge_session_reuse_window_seconds(dashboard_settings),
        "stale_operation_abandonment_window": max(_STALE_OPERATION_ABANDONMENT_FLOOR_SECONDS, bridge_request_budget),
        "claimed_retry_circuit_lifetime": _CLAIMED_RETRY_CIRCUIT_TTL_MULTIPLIER
        * float(DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS),
    }


def operation_spool_retention_floor_seconds(
    dashboard_settings: object,
    *,
    startup_settings: object | None = None,
) -> float:
    """Lowest spool retention that still covers every replay window."""
    return max(spool_retention_floor_terms_seconds(dashboard_settings, startup_settings=startup_settings).values())


def binding_spool_retention_floor_term(
    dashboard_settings: object,
    *,
    startup_settings: object | None = None,
) -> tuple[str, float]:
    """``(term name, seconds)`` of the window that binds the floor, for error messages."""
    terms = spool_retention_floor_terms_seconds(dashboard_settings, startup_settings=startup_settings)
    name = max(terms, key=lambda key: terms[key])
    return name, terms[name]


def resolve_operation_spool_retention_seconds(
    dashboard_settings: object | None,
    *,
    startup_settings: object | None = None,
) -> float:
    """Effective spool retention from a dashboard-settings snapshot.

    ``dashboard_settings`` is a ``DashboardSettings`` row (or any object
    exposing the same attribute; a missing attribute reads as NULL).
    ``startup_settings`` supplies the environment layer when the caller already
    holds it; it defaults to ``get_settings()``.
    """
    startup = get_settings() if startup_settings is None else startup_settings
    return _effective_seconds(
        dashboard_settings,
        OPERATION_SPOOL_RETENTION_SETTING,
        float(getattr(startup, OPERATION_SPOOL_RETENTION_SETTING, _OPERATION_SPOOL_RETENTION_DEFAULT)),
        _OPERATION_SPOOL_RETENTION_DEFAULT,
    )


def warn_spool_retention_below_floor(
    dashboard_settings: object,
    startup_settings: object | None = None,
) -> tuple[str, float, float] | None:
    """Warn once at startup when the effective spool retention is below its floor.

    The environment alias alone can put a deployment below the floor while the
    dashboard column is NULL, and the settings API never saw that value. Warning
    here means the operator learns about it before a dashboard edit is refused.
    Returns ``(binding term, effective retention, floor)`` when it warned, so the
    caller can assert on it; ``None`` otherwise. Only these two numbers and the
    term name are logged -- never the value of any other setting.
    """
    retention = resolve_operation_spool_retention_seconds(dashboard_settings, startup_settings=startup_settings)
    term, floor = binding_spool_retention_floor_term(dashboard_settings, startup_settings=startup_settings)
    if retention >= floor:
        return None
    logger.warning(
        "HTTP bridge operation spool retention is below its replay floor: %s=%gs < %gs (bound by %s). "
        "Expired transcripts can be deleted while a recovery can still replay them; "
        "raise the value in Settings -> Data retention. Dashboard updates that lower it further are refused.",
        OPERATION_SPOOL_RETENTION_SETTING,
        retention,
        floor,
        term,
    )
    return term, retention, floor
