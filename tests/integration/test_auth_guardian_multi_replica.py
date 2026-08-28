"""Auth Guardian dynamic multi-replica detection.

Pre-change the guardian's only multi-replica guard was the static bridge
instance ring computed at build time — Helm and compose deployments leave that
ring empty, so with leader election disabled every replica ran the guardian's
concurrent force-refreshes. The per-tick dynamic check counts live
``bridge_ring_members`` heartbeats instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.auth.guardian import AuthGuardianScheduler
from app.core.utils.time import utcnow
from app.db.models import Account, BridgeRingMember
from app.db.session import SessionLocal
from app.modules.proxy.ring_membership import RingMembershipService

pytestmark = pytest.mark.integration


class _RecordingRepo:
    def __init__(self) -> None:
        self.list_calls = 0

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        self.list_calls += 1
        return []

    async def get_by_id(self, account_id: str) -> Account | None:
        return None


class _AlwaysLeader:
    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
        return await fn()


def _scheduler(repo: _RecordingRepo, *, leader_election_enabled: bool) -> AuthGuardianScheduler:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_RecordingRepo]:
        yield repo

    return AuthGuardianScheduler(
        interval_seconds=3600,
        enabled=True,
        max_age_seconds=3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_enabled=leader_election_enabled,
        leader_election_factory=lambda: _AlwaysLeader(),
        repo_factory=repo_factory,
    )


async def _age_heartbeat(instance_id: str, *, seconds: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(BridgeRingMember)
            .where(BridgeRingMember.instance_id == instance_id)
            .values(last_heartbeat_at=utcnow() - timedelta(seconds=seconds))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_guardian_skips_pass_with_dynamic_replicas_and_no_leader_election(db_setup):
    """Failed pre-change: with an empty static ring the guardian ran its
    refresh pass on every replica even though two live replicas were
    registered dynamically."""
    ring = RingMembershipService(SessionLocal)
    await ring.register("pod-a")
    await ring.register("pod-b")

    repo = _RecordingRepo()
    scheduler = _scheduler(repo, leader_election_enabled=False)

    await scheduler._refresh_once()
    assert repo.list_calls == 0

    # Once only one replica's heartbeat is live, the pass runs again.
    await _age_heartbeat("pod-b", seconds=120)
    await scheduler._refresh_once()
    assert repo.list_calls == 1


@pytest.mark.asyncio
async def test_guardian_runs_pass_with_leader_election_enabled(db_setup):
    ring = RingMembershipService(SessionLocal)
    await ring.register("pod-a")
    await ring.register("pod-b")

    repo = _RecordingRepo()
    scheduler = _scheduler(repo, leader_election_enabled=True)

    await scheduler._refresh_once()
    assert repo.list_calls == 1
