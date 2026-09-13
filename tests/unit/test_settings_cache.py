from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.core.config.settings_cache as settings_cache_module
from app.core.config.settings_cache import SettingsCache

pytestmark = pytest.mark.unit


class _FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_settings_cache_ttl_and_invalidate(monkeypatch) -> None:
    state = {"now": 100.0, "calls": 0}

    class _FakeRepository:
        def __init__(self, _session) -> None:
            pass

        async def get_or_create(self):
            state["calls"] += 1
            return SimpleNamespace(version=state["calls"])

    monkeypatch.setattr(settings_cache_module, "SessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(settings_cache_module, "SettingsRepository", _FakeRepository)
    monkeypatch.setattr(settings_cache_module.time, "monotonic", lambda: state["now"])

    cache = SettingsCache(ttl_seconds=5.0)

    first = await cache.get()
    second = await cache.get()
    assert first is second
    assert state["calls"] == 1

    state["now"] = 106.0
    third = await cache.get()
    assert third is not first
    assert state["calls"] == 2

    await cache.invalidate()
    fourth = await cache.get()
    assert fourth is not third
    assert state["calls"] == 3


@pytest.mark.asyncio
async def test_cached_row_survives_invalidate(monkeypatch) -> None:
    """An invalidation expires the snapshot for ``get`` but does not forget it.

    The fallback readers (`dashboard_overrides` when the database is
    unreadable, the conversation archive gate) must not revert to the
    environment values in the window between an invalidation and the next
    load: for the archive that would let the deprecated environment alias
    resume recording an operator just turned off.
    """
    state = {"now": 100.0, "calls": 0}

    class _FakeRepository:
        def __init__(self, _session) -> None:
            pass

        async def get_or_create(self):
            state["calls"] += 1
            return SimpleNamespace(version=state["calls"], conversation_archive_enabled=False)

    monkeypatch.setattr(settings_cache_module, "SessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(settings_cache_module, "SettingsRepository", _FakeRepository)
    monkeypatch.setattr(settings_cache_module.time, "monotonic", lambda: state["now"])

    cache = SettingsCache(ttl_seconds=5.0)
    assert cache.cached_row() is None  # nothing loaded yet: callers fall back to the environment

    loaded = await cache.get()
    await cache.invalidate()

    assert cache.cached_row() is loaded
    assert state["calls"] == 1  # reading the stale row never hits the database

    reloaded = await cache.get()
    assert reloaded is not loaded
    assert cache.cached_row() is reloaded


@pytest.mark.asyncio
async def test_refresh_loads_and_replaces_the_snapshot(monkeypatch) -> None:
    """``refresh`` is the bus callback: it pulls the new row in without a caller."""
    state = {"now": 100.0, "calls": 0}

    class _FakeRepository:
        def __init__(self, _session) -> None:
            pass

        async def get_or_create(self):
            state["calls"] += 1
            return SimpleNamespace(version=state["calls"])

    monkeypatch.setattr(settings_cache_module, "SessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(settings_cache_module, "SettingsRepository", _FakeRepository)
    monkeypatch.setattr(settings_cache_module.time, "monotonic", lambda: state["now"])

    cache = SettingsCache(ttl_seconds=5.0)
    first = await cache.get()

    refreshed = await cache.refresh()
    assert refreshed is not first
    assert cache.cached_row() is refreshed
    assert await cache.get() is refreshed  # refreshed rows are current, not stale
    assert state["calls"] == 2
