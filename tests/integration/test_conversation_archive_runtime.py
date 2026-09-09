from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.core import conversation_archive
from app.core.config.settings_cache import get_settings_cache

pytestmark = pytest.mark.integration


class _ArchiveEnvironment:
    """Startup ``Settings`` double: the env alias is off, the archive dir is a temp shard."""

    def __init__(self, directory: Path) -> None:
        self.conversation_archive_enabled = False
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
