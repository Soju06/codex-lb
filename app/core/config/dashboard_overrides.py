"""Request-scoped dashboard overrides for ``Settings`` fields that moved to the dashboard.

Precedence is fixed by ``configuration-tiers``: code default < environment <
dashboard. ``Settings`` already folds the first two layers; this module adds the
third for the fields that have a nullable ``dashboard_settings`` column of the
same name (a non-NULL column wins, NULL inherits the environment value).

Hot paths must not read the database: the request entry point (the
``DashboardOverridesMiddleware``) reads the ``SettingsCache`` snapshot once and
binds the overrides to a ``ContextVar``; consumers then call
``with_dashboard_overrides(get_settings())`` (the proxy service facade does this
for every ``_service_get_settings()`` caller) and read fields as before. Outside
a bound context — startup, schedulers, unit tests — the base ``Settings`` is
returned unchanged, so the environment fallback keeps applying there.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Final

from app.core.config.settings import Settings

if TYPE_CHECKING:
    from app.db.models import DashboardSettings

# C2-1 timeouts: upstream timeouts and request budgets managed from the
# dashboard (Settings -> Advanced -> Upstream timeouts). The ``Settings`` field
# is the deprecated env alias; the ``dashboard_settings`` column of the same
# name wins when it is non-NULL.
DASHBOARD_TIMEOUT_SETTINGS: Final[tuple[str, ...]] = (
    "upstream_connect_timeout_seconds",
    "proxy_request_budget_seconds",
    "compact_request_budget_seconds",
    "transcription_request_budget_seconds",
    "stream_idle_timeout_seconds",
    "proxy_downstream_websocket_idle_timeout_seconds",
    "sse_keepalive_interval_seconds",
    # M1 stream/bridge budgets: the Responses stream request budget and the
    # HTTP session bridge request budget share the Upstream timeouts card.
    "http_responses_stream_request_budget_seconds",
    "http_responses_session_bridge_request_budget_seconds",
    # end M1 stream/bridge budgets
)

# M3 codex prewarm: dashboard-managed behaviour switches, overlaid exactly like
# the timeouts. Keeping them here means their single consumer reads the field
# off ``Settings`` as before -- no extra snapshot read, and in particular no
# ``await`` in the hot path that reads them.
DASHBOARD_SWITCH_SETTINGS: Final[tuple[str, ...]] = ("http_responses_session_bridge_codex_prewarm_enabled",)

# Dashboard-managed *enumerated* values, overlaid the same way. Unlike the
# switches these are strings, which is why ``DashboardOverrideValue`` admits
# ``str``: the thread cache identity mode has to be overlaid here and not only
# folded into the settings API response, or the proxy would keep serving
# ``shared`` while ``GET /api/settings`` reported the operator's choice as the
# effective value.
DASHBOARD_MODE_SETTINGS: Final[tuple[str, ...]] = ("thread_cache_identity_mode",)

# Every ``Settings`` field whose dashboard column overrides the environment
# value at runtime (the account-capacity caps have their own consumer path
# through ``SettingsService``, and the resilience toggles their own task-bound
# ``ResilienceToggles``; neither is overlaid here).
DASHBOARD_OVERRIDE_SETTINGS: Final[tuple[str, ...]] = (
    DASHBOARD_TIMEOUT_SETTINGS + DASHBOARD_SWITCH_SETTINGS + DASHBOARD_MODE_SETTINGS
)

type DashboardOverrideValue = float | bool | str


def dashboard_overrides(row: DashboardSettings) -> dict[str, DashboardOverrideValue]:
    """Non-NULL dashboard values keyed by ``Settings`` field name."""
    overrides: dict[str, DashboardOverrideValue] = {}
    for name in DASHBOARD_OVERRIDE_SETTINGS:
        value = getattr(row, name, None)
        if value is not None:
            overrides[name] = value
    return overrides


class DashboardOverlay:
    """Overrides bound to one request, with the overlaid ``Settings`` memoised per base object.

    ``Settings`` is a process-wide cached singleton, so the copy is built once
    per request; a test that swaps ``get_settings`` gets its own overlay.
    """

    __slots__ = ("_applied", "_base", "overrides")

    def __init__(self, overrides: Mapping[str, DashboardOverrideValue]) -> None:
        self.overrides: Mapping[str, DashboardOverrideValue] = dict(overrides)
        self._base: Settings | None = None
        self._applied: Settings | None = None

    def apply(self, base: Settings) -> Settings:
        if not self.overrides:
            return base
        if self._applied is not None and self._base is base:
            return self._applied
        applied = base.model_copy(update=dict(self.overrides))
        self._base = base
        self._applied = applied
        return applied


_OVERLAY: contextvars.ContextVar[DashboardOverlay | None] = contextvars.ContextVar(
    "dashboard_settings_overlay", default=None
)


def with_dashboard_overrides(base: Settings) -> Settings:
    """``base`` with the request-bound dashboard values applied (``base`` itself when none are bound)."""
    overlay = _OVERLAY.get()
    if overlay is None:
        return base
    return overlay.apply(base)


def effective_settings(row: DashboardSettings, base: Settings) -> Settings:
    """``base`` with ``row``'s non-NULL dashboard values applied, independent of the bound context.

    For code that already holds a dashboard snapshot outside a request (startup
    hooks, background loops) and must honour the dashboard the same way the
    request path does.
    """
    return DashboardOverlay(dashboard_overrides(row)).apply(base)


def bind_dashboard_overrides(row: DashboardSettings) -> contextvars.Token[DashboardOverlay | None]:
    return _OVERLAY.set(DashboardOverlay(dashboard_overrides(row)))


def reset_dashboard_overrides(token: contextvars.Token[DashboardOverlay | None]) -> None:
    _OVERLAY.reset(token)


@contextlib.contextmanager
def dashboard_overrides_bound(row: DashboardSettings) -> Iterator[None]:
    token = bind_dashboard_overrides(row)
    try:
        yield
    finally:
        reset_dashboard_overrides(token)
