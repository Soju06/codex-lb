from __future__ import annotations

import logging
from typing import Any, cast

import pytest

import app.core.middleware.dashboard_overrides as middleware_module
from app.core.config.dashboard_overrides import (
    DASHBOARD_SWITCH_SETTINGS,
    DASHBOARD_TIMEOUT_SETTINGS,
    dashboard_overrides,
    dashboard_overrides_bound,
    effective_settings,
    with_dashboard_overrides,
)
from app.core.config.settings import Settings
from app.core.middleware.dashboard_overrides import DashboardOverridesMiddleware
from app.db.models import DashboardSettings
from app.modules.settings.service import warn_environment_shadowed_by_dashboard

pytestmark = pytest.mark.unit


def _row(**columns: float) -> DashboardSettings:
    row = DashboardSettings()
    for name in DASHBOARD_TIMEOUT_SETTINGS:
        setattr(row, name, columns.get(name))
    return row


def test_dashboard_overrides_returns_only_non_null_timeout_columns() -> None:
    row = _row(proxy_request_budget_seconds=900.0, sse_keepalive_interval_seconds=0.0)

    assert dashboard_overrides(row) == {"proxy_request_budget_seconds": 900.0, "sse_keepalive_interval_seconds": 0.0}


def test_dashboard_switch_column_overrides_the_environment_alias() -> None:
    """M3 codex prewarm: the behaviour switch rides the same overlay as the
    timeouts, so its consumer keeps reading it off ``Settings`` -- no extra
    snapshot read, and no ``await`` in the path that reads it."""
    name = "http_responses_session_bridge_codex_prewarm_enabled"
    assert name in DASHBOARD_SWITCH_SETTINGS
    env_off = Settings().model_copy(update={name: False})
    env_on = Settings().model_copy(update={name: True})
    row = DashboardSettings()

    row.http_responses_session_bridge_codex_prewarm_enabled = True
    assert dashboard_overrides(row) == {name: True}
    with dashboard_overrides_bound(row):
        assert getattr(with_dashboard_overrides(env_off), name) is True

    # ``false`` is a dashboard value, not "unset": it wins over an enabled alias.
    row.http_responses_session_bridge_codex_prewarm_enabled = False
    assert dashboard_overrides(row) == {name: False}
    with dashboard_overrides_bound(row):
        assert getattr(with_dashboard_overrides(env_on), name) is False

    # NULL column: the deprecated environment alias keeps applying.
    row.http_responses_session_bridge_codex_prewarm_enabled = None
    assert dashboard_overrides(row) == {}
    with dashboard_overrides_bound(row):
        assert getattr(with_dashboard_overrides(env_on), name) is True
        assert getattr(with_dashboard_overrides(env_off), name) is False


def test_with_dashboard_overrides_is_identity_outside_a_bound_context() -> None:
    base = Settings()

    assert with_dashboard_overrides(base) is base


def test_bound_overrides_win_over_environment_and_leave_other_fields_untouched() -> None:
    base = Settings().model_copy(update={"proxy_request_budget_seconds": 601.0, "stream_idle_timeout_seconds": 50.0})
    row = _row(proxy_request_budget_seconds=900.0)

    with dashboard_overrides_bound(row):
        effective = with_dashboard_overrides(base)
        assert effective.proxy_request_budget_seconds == 900.0
        # NULL column: the environment value keeps applying.
        assert effective.stream_idle_timeout_seconds == 50.0
        assert effective.upstream_base_url == base.upstream_base_url
        # One overlay per request: the copy is built once for the same base.
        assert with_dashboard_overrides(base) is effective
        # A swapped base (tests monkeypatching get_settings) gets its own overlay.
        other = Settings().model_copy(update={"proxy_request_budget_seconds": 7.0})
        assert with_dashboard_overrides(other).proxy_request_budget_seconds == 900.0
        assert with_dashboard_overrides(other) is not effective

    # The binding does not leak past the context and never mutates the base.
    assert with_dashboard_overrides(base) is base
    assert base.proxy_request_budget_seconds == 601.0


def test_effective_settings_applies_a_snapshot_without_binding() -> None:
    base = Settings()
    row = _row(upstream_connect_timeout_seconds=3.5)

    assert effective_settings(row, base).upstream_connect_timeout_seconds == 3.5
    assert effective_settings(_row(), base) is base


@pytest.mark.asyncio
async def test_middleware_binds_snapshot_for_http_scope_and_resets_after(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row(sse_keepalive_interval_seconds=0.25)

    class _Cache:
        async def get(self) -> DashboardSettings:
            return row

    monkeypatch.setattr(middleware_module, "get_settings_cache", lambda: _Cache())
    base = Settings()
    seen: list[float] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(with_dashboard_overrides(base).sse_keepalive_interval_seconds)

    middleware = DashboardOverridesMiddleware(app)
    await middleware({"type": "http", "path": "/v1/responses"}, cast(Any, None), cast(Any, None))
    await middleware({"type": "lifespan"}, cast(Any, None), cast(Any, None))

    assert seen == [0.25, base.sse_keepalive_interval_seconds]
    assert with_dashboard_overrides(base) is base


@pytest.mark.asyncio
async def test_middleware_falls_back_to_environment_when_snapshot_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenCache:
        def __init__(self) -> None:
            self.last_row: DashboardSettings | None = None

        async def get(self) -> DashboardSettings:
            raise RuntimeError("database unavailable")

        def cached_row(self) -> DashboardSettings | None:
            return self.last_row

    cache = _BrokenCache()
    monkeypatch.setattr(middleware_module, "get_settings_cache", lambda: cache)
    base = Settings()
    seen: list[float] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(with_dashboard_overrides(base).proxy_request_budget_seconds)

    middleware = DashboardOverridesMiddleware(app)
    # Nothing loaded yet: the environment value applies.
    await middleware({"type": "http", "path": "/"}, cast(Any, None), cast(Any, None))
    # A refresh failure after a successful load keeps the last dashboard values.
    cache.last_row = _row(proxy_request_budget_seconds=900.0)
    await middleware({"type": "http", "path": "/"}, cast(Any, None), cast(Any, None))

    assert seen == [base.proxy_request_budget_seconds, 900.0]


def test_warn_environment_shadowed_by_dashboard_only_when_env_is_set_and_column_is_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Explicitly "set" two env aliases; only the one with a dashboard value is shadowed.
    settings = Settings(proxy_request_budget_seconds=601.0, compact_request_budget_seconds=200.0)
    row = _row(proxy_request_budget_seconds=900.0, sse_keepalive_interval_seconds=2.0)

    with caplog.at_level(logging.WARNING, logger="app.modules.settings.service"):
        shadowed = warn_environment_shadowed_by_dashboard(row, settings)

    assert shadowed == ["proxy_request_budget_seconds"]
    assert "CODEX_LB_PROXY_REQUEST_BUDGET_SECONDS" in caplog.text
    assert "SSE_KEEPALIVE" not in caplog.text

    caplog.clear()
    assert warn_environment_shadowed_by_dashboard(_row(), settings) == []
    assert caplog.text == ""
