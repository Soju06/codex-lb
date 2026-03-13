from __future__ import annotations

from types import SimpleNamespace

import app.modules.sticky_sessions.cleanup_scheduler as cleanup_scheduler


def test_build_sticky_session_cleanup_scheduler_respects_enabled_setting(monkeypatch) -> None:
    settings = SimpleNamespace(sticky_session_cleanup_interval_seconds=42, sticky_session_cleanup_enabled=False)
    monkeypatch.setattr(cleanup_scheduler, "get_settings", lambda: settings)

    scheduler = cleanup_scheduler.build_sticky_session_cleanup_scheduler()

    assert scheduler.interval_seconds == 42
    assert scheduler.enabled is False
