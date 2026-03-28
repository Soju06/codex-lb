from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TypedDict

import pytest

leader_election_module = importlib.import_module("app.core.scheduling.leader_election")

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_try_acquire_returns_true_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        leader_election_enabled=False,
        database_url="postgresql+asyncpg://db",
        leader_election_ttl_seconds=30,
    )
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True


@pytest.mark.asyncio
async def test_try_acquire_returns_true_for_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        leader_election_enabled=True,
        database_url="sqlite+aiosqlite:///tmp/test.db",
        leader_election_ttl_seconds=30,
    )
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True


class _LeaseSession:
    def __init__(self, shared: "_SharedLease", lock: asyncio.Lock) -> None:
        self._shared = shared
        self._lock = lock

    async def execute(self, _statement: object, params: dict[str, object]) -> object:
        async with self._lock:
            row = self._shared.row
            now_obj = params["now"]
            leader_id_obj = params["leader_id"]
            expires_at_obj = params["expires_at"]

            assert isinstance(now_obj, datetime)
            assert isinstance(leader_id_obj, str)
            assert isinstance(expires_at_obj, datetime)

            now = now_obj
            leader_id = leader_id_obj
            expires_at = expires_at_obj

            if row is None:
                new_row: _LeaseRow = {"leader_id": leader_id, "expires_at": expires_at}
                self._shared.row = new_row
            else:
                expired = row["expires_at"] < now
                same_leader = row["leader_id"] == leader_id
                if expired or same_leader:
                    row["leader_id"] = leader_id
                    row["expires_at"] = expires_at

        return object()

    async def commit(self) -> None:
        return None

    async def scalar(self, _statement: object) -> str | None:
        row = self._shared.row
        if row is None:
            return None
        return row["leader_id"]


class _LeaseRow(TypedDict):
    leader_id: str
    expires_at: datetime


class _SharedLease:
    def __init__(self, row: _LeaseRow | None = None) -> None:
        self.row = row


def _build_session_provider(shared: _SharedLease, lock: asyncio.Lock):
    async def _provider():
        yield _LeaseSession(shared, lock)

    return _provider


@pytest.mark.asyncio
async def test_try_acquire_concurrent_only_one_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        leader_election_enabled=True,
        database_url="postgresql+asyncpg://db",
        leader_election_ttl_seconds=30,
    )
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)

    shared = _SharedLease()
    lock = asyncio.Lock()
    monkeypatch.setattr(leader_election_module, "get_session", _build_session_provider(shared, lock))

    election_a = leader_election_module.LeaderElection(leader_id="node-a")
    election_b = leader_election_module.LeaderElection(leader_id="node-b")

    result_a, result_b = await asyncio.gather(election_a.try_acquire(), election_b.try_acquire())

    assert (result_a, result_b).count(True) == 1
    assert (result_a, result_b).count(False) == 1


@pytest.mark.asyncio
async def test_try_acquire_sets_is_leader_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        leader_election_enabled=True,
        database_url="postgresql+asyncpg://db",
        leader_election_ttl_seconds=30,
    )
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)

    now = datetime.now(UTC)
    shared = _SharedLease(row={"leader_id": "node-a", "expires_at": now + timedelta(seconds=30)})
    lock = asyncio.Lock()
    monkeypatch.setattr(leader_election_module, "get_session", _build_session_provider(shared, lock))

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True
    assert election._is_leader is True
