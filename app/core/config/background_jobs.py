"""Dashboard-managed background job toggles (M2, slop-removal 0908).

``auth_guardian_enabled``, ``automations_scheduler_enabled`` and
``rate_limit_reset_credits_refresh_enabled`` are nullable ``dashboard_settings``
columns that inherit the deprecated ``CODEX_LB_*`` environment alias and then
the code default, resolved through :func:`resolve_inheritable`.

The schedulers always start their loop; each cycle reads the ``SettingsCache``
snapshot at its entry and skips the pass while the effective value is False, so
a dashboard change takes effect on the next tick without a restart. Nothing
here reads the database inside a runtime lock or per-item loop: the snapshot is
taken once per cycle, before the scheduler's lock.
"""

from __future__ import annotations

from typing import Final

from app.core.config.inheritable import resolve_inheritable
from app.core.config.settings import Settings, get_settings

# ``dashboard_settings`` column, ``Settings`` field and provenance key share
# one name (configuration-tiers).
BACKGROUND_JOB_SETTINGS: Final[tuple[str, ...]] = (
    "auth_guardian_enabled",
    "automations_scheduler_enabled",
    "rate_limit_reset_credits_refresh_enabled",
)


def resolve_background_job_toggle(
    dashboard_settings: object | None,
    name: str,
    *,
    startup_settings: object | None = None,
) -> bool:
    """Effective value of one background job toggle from a dashboard-settings snapshot.

    ``dashboard_settings`` is the cached ``DashboardSettings`` row (or any object
    exposing the same attribute; a missing attribute reads as NULL).
    ``startup_settings`` supplies the environment layer when the caller already
    holds it; it defaults to ``get_settings()``.
    """
    default = bool(Settings.model_fields[name].default)
    startup = get_settings() if startup_settings is None else startup_settings
    return bool(
        resolve_inheritable(
            getattr(dashboard_settings, name, None),
            bool(getattr(startup, name, default)),
            default,
        ).value
    )


async def background_job_enabled(name: str, *, startup_settings: object | None = None) -> bool:
    """Effective toggle from the shared ``SettingsCache`` snapshot (one read per scheduler cycle)."""
    from app.core.config.settings_cache import get_settings_cache

    return resolve_background_job_toggle(await get_settings_cache().get(), name, startup_settings=startup_settings)


def auth_guardian_blocked_by_topology(settings: Settings | None = None) -> bool:
    """True when the static topology forbids the Auth Guardian regardless of its toggle.

    Concurrent force token refreshes across replicas can invalidate rotated
    refresh tokens, so without leader election the guardian must not run in a
    multi-replica ring at all (usage-refresh-policy "Multi-replica leader
    guard"). The dashboard toggle cannot override this; the settings API
    reports it so the UI can say why the guardian is idle.
    """
    startup = get_settings() if settings is None else settings
    multi_replica = len(startup.http_responses_session_bridge_instance_ring) > 1
    return multi_replica and not startup.leader_election_enabled
