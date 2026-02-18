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
