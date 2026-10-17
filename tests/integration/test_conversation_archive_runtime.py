from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.core import conversation_archive
from app.core.cache.invalidation import NAMESPACE_SETTINGS, CacheInvalidationPoller
from app.core.config.settings_cache import SettingsCache, get_settings_cache
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


class _ArchiveEnvironment:
    """Startup ``Settings`` double: the deprecated env alias, and a temp archive shard."""

    def __init__(self, directory: Path, *, enabled: bool = False) -> None:
        self.conversation_archive_enabled = enabled
        self.conversation_archive_dir = directory
        self.conversation_archive_queue_max_bytes = 8 * 1024 * 1024


def _records(directory: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            records.extend(json.loads(line) for line in fh.read().splitlines())
    return records


def _archive_one(request_id: str) -> None:
    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"input": request_id},
        extra={"marker": request_id},
    )
    conversation_archive.flush_archive_writer()


@pytest.mark.asyncio
async def test_dashboard_toggle_starts_and_stops_archiving_without_a_restart(async_client, monkeypatch, tmp_path):
    """M5 conversation archive: enable -> the next request is archived; disable -> new requests are not.

    Drives the single ``archive_enabled()`` gate every upstream-client call site
    goes through, with the real settings cache: the dashboard PUT invalidates the
    cache, the next snapshot load carries the new column value, and no restart
    or environment change is involved.
    """
    cache = get_settings_cache()
    monkeypatch.setattr(conversation_archive, "get_settings", lambda: _ArchiveEnvironment(tmp_path))
    try:
        await cache.get()
        _archive_one("before-enable")
        assert _records(tmp_path) == []

        enabled = await async_client.put("/api/settings", json={"conversationArchiveEnabled": True})
        assert enabled.status_code == 200
        await cache.get()  # the PUT invalidated the cache; load the new snapshot
        _archive_one("while-enabled")
        assert [record["extra"] for record in _records(tmp_path)] == [{"marker": "while-enabled"}]

        disabled = await async_client.put("/api/settings", json={"conversationArchiveEnabled": False})
        assert disabled.status_code == 200
        await cache.get()
        _archive_one("after-disable")
        assert [record["extra"] for record in _records(tmp_path)] == [{"marker": "while-enabled"}]
    finally:
        await cache.invalidate(propagate=False)


@pytest.mark.asyncio
async def test_dashboard_off_survives_a_cache_invalidation_with_the_env_alias_on(async_client, monkeypatch, tmp_path):
    """A settings mutation must not let the deprecated env alias resume recording.

    Every ``PUT /api/settings`` invalidates the settings cache, and a replica
    serving only long-lived WebSocket traffic may not load a fresh snapshot for
    a long time. The gate therefore reads the last loaded row rather than
    forgetting it, or an operator who turned the archive off on a host that
    still sets ``CODEX_LB_CONVERSATION_ARCHIVE_ENABLED=true`` would silently
    start recording again.
    """
    cache = get_settings_cache()
    monkeypatch.setattr(conversation_archive, "get_settings", lambda: _ArchiveEnvironment(tmp_path, enabled=True))
    try:
        turned_off = await async_client.put("/api/settings", json={"conversationArchiveEnabled": False})
        assert turned_off.status_code == 200
        await cache.get()
        assert conversation_archive.archive_enabled() is False

        unrelated = await async_client.put("/api/settings", json={"warmupModel": "gpt-5.6-sol"})
        assert unrelated.status_code == 200
        await cache.invalidate(propagate=False)  # the state right after any mutation

        assert cache.cached_row() is not None
        assert conversation_archive.archive_enabled() is False
        _archive_one("after-invalidate")
        assert _records(tmp_path) == []
    finally:
        await cache.invalidate(propagate=False)


@pytest.mark.asyncio
async def test_open_stream_replica_stops_recording_after_a_peer_disables_it(
    async_client, db_setup, monkeypatch, tmp_path
):
    """A replica carrying only an already-open stream must follow a peer's disable.

    The gate cannot await, so a replica that receives no new request would keep
    the snapshot it loaded when the stream opened. The settings cache is
    refreshed off the cache-invalidation bus for exactly this reason: one poll
    cycle after the peer's ``PUT``, frames of the open stream stop being
    archived — no new request, no restart.
    """
    replica_b = SettingsCache()
    poller_b = CacheInvalidationPoller(SessionLocal)
    poller_b.on_invalidation(NAMESPACE_SETTINGS, lambda: replica_b.invalidate(propagate=False))
    poller_b.on_invalidation(NAMESPACE_SETTINGS, replica_b.refresh)
    await poller_b._poll_once()

    # The env alias is on, so a gate that fell back to it would keep recording.
    monkeypatch.setattr(conversation_archive, "get_settings", lambda: _ArchiveEnvironment(tmp_path, enabled=True))
    monkeypatch.setattr(conversation_archive, "get_settings_cache", lambda: replica_b)

    enabled = await async_client.put("/api/settings", json={"conversationArchiveEnabled": True})
    assert enabled.status_code == 200
    await poller_b._poll_once()
    assert conversation_archive.archive_enabled() is True
    _archive_one("open-stream-frame")
    assert [record["extra"] for record in _records(tmp_path)] == [{"marker": "open-stream-frame"}]

    # The peer turns it off. Replica B serves no request in between.
    disabled = await async_client.put("/api/settings", json={"conversationArchiveEnabled": False})
    assert disabled.status_code == 200
    await poller_b._poll_once()

    assert conversation_archive.archive_enabled() is False
    _archive_one("frame-after-disable")
    assert [record["extra"] for record in _records(tmp_path)] == [{"marker": "open-stream-frame"}]
