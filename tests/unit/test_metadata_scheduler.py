from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.clients.codex_version import CodexVersionCache
from app.core.usage import metadata_scheduler as module
from app.core.usage import pricing_catalog as catalog
from app.core.usage.pricing import ModelPrice, get_pricing_for_model


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "_prices", None)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path))
    cache = CodexVersionCache()
    monkeypatch.setattr(cache, "_fetch_latest_version", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "get_codex_version_cache", lambda: cache)
    return tmp_path


async def test_refresh_persists_and_offline_restart_restores(setup, monkeypatch):
    monkeypatch.setattr(module, "fetch_catalogs", AsyncMock(return_value={"gpt-test": ModelPrice(3, 9)}))
    first = module.MetadataRefreshScheduler()
    await first._refresh()
    assert get_pricing_for_model("gpt-test") == ("gpt-test", ModelPrice(3, 9))
    assert json.loads((setup / "pricing-cache.json").read_text())["models"]["gpt-test"]["input_per_1m"] == 3
    monkeypatch.setattr(catalog, "_prices", None)
    monkeypatch.setattr(module, "fetch_catalogs", AsyncMock(side_effect=ValueError("offline")))
    second = module.MetadataRefreshScheduler()
    monkeypatch.setattr(second, "_run_loop", AsyncMock())
    await second.start()
    await second._refresh()
    assert get_pricing_for_model("gpt-test") == ("gpt-test", ModelPrice(3, 9))
    await second.stop()
    assert second._task is None


async def test_stop_cancels_owned_fetch_and_no_follower_backfill(setup, monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def fetch():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(module, "fetch_catalogs", fetch)
    leader = SimpleNamespace(run_if_leader=AsyncMock(return_value=False))
    monkeypatch.setattr(module, "get_leader_election", lambda: leader)
    scheduler = module.MetadataRefreshScheduler()
    await scheduler.start()
    await asyncio.wait_for(entered.wait(), timeout=1)
    await scheduler.stop()
    assert cancelled.is_set()
    leader.run_if_leader.assert_not_called()


async def test_newer_bundle_beats_old_disk_cache(setup, monkeypatch):
    stale = json.loads(catalog.encode_snapshot({"gpt-6-astra": ModelPrice(1, 2)}))
    stale["updated_at"] = "2000-01-01T00:00:00+00:00"
    (setup / "pricing-cache.json").write_text(json.dumps(stale))
    scheduler = module.MetadataRefreshScheduler()
    monkeypatch.setattr(scheduler, "_run_loop", AsyncMock())
    await scheduler.start()
    bundled = catalog.decode_snapshot(json.loads(catalog.BUNDLE_PATH.read_text()))
    assert get_pricing_for_model("gpt-6-astra") == ("gpt-6-astra", bundled["gpt-6-astra"])
    await scheduler.stop()


async def test_follower_refreshes_prices_without_running_backfill(setup, monkeypatch):
    refreshed = asyncio.Event()
    finished = asyncio.Event()

    async def fetch():
        refreshed.set()
        return {"gpt-test": ModelPrice(3, 9)}

    async def run_if_leader(callback):
        finished.set()
        return False

    monkeypatch.setattr(module, "fetch_catalogs", fetch)
    monkeypatch.setattr(module, "get_leader_election", lambda: SimpleNamespace(run_if_leader=run_if_leader))
    scheduler = module.MetadataRefreshScheduler()
    backfill = AsyncMock()
    monkeypatch.setattr(scheduler, "_backfill", backfill)
    await scheduler.start()
    await asyncio.wait_for(finished.wait(), timeout=1)
    await scheduler.stop()
    assert refreshed.is_set()
    assert get_pricing_for_model("gpt-test") == ("gpt-test", ModelPrice(3, 9))
    backfill.assert_not_awaited()


async def test_compatible_partial_refresh_does_not_restart_backfill_cursor(setup, monkeypatch):
    catalog.install_prices({"gpt-test": ModelPrice(10, 50, 1, flex_input_per_1m=5, flex_output_per_1m=25)})
    monkeypatch.setattr(module, "fetch_catalogs", AsyncMock(return_value={"gpt-test": ModelPrice(10, 50, 1)}))
    scheduler = module.MetadataRefreshScheduler(_cursor=123)
    await scheduler._refresh()
    assert scheduler._cursor == 123
