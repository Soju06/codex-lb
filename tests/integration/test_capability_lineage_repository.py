from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert

from app.db.models import CapabilityLineageMarker
from app.db.session import SessionLocal
from app.modules.proxy.capability_lineage import (
    CapabilityLineageAlias,
    capability_lineage_marker_hash,
)
from app.modules.proxy.capability_lineage_repository import CapabilityLineageRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_capability_lineage_is_durable_scope_isolated_and_opaque(db_setup) -> None:
    aliases = (
        CapabilityLineageAlias(kind="session_header", value="visible-session-id"),
        CapabilityLineageAlias(kind="turn_state", value="visible-turn-state"),
    )

    async with SessionLocal() as session:
        repository = CapabilityLineageRepository(session)
        marker_hashes = await repository.require(
            capability="trusted_cyber",
            api_key_scope="api-key-a",
            aliases=aliases,
        )

        assert len(marker_hashes) == 2
        stored_markers = (await session.execute(select(CapabilityLineageMarker))).scalars().all()
        assert {marker.marker_hash for marker in stored_markers} == set(marker_hashes)
        assert all("visible-session-id" not in marker.marker_hash for marker in stored_markers)
        assert all("visible-turn-state" not in marker.marker_hash for marker in stored_markers)
        assert all("api-key-a" not in marker.marker_hash for marker in stored_markers)

    async with SessionLocal() as fresh_session:
        fresh_repository = CapabilityLineageRepository(fresh_session)

        assert await fresh_repository.is_required(
            capability="trusted_cyber",
            api_key_scope="api-key-a",
            aliases=aliases,
        )
        assert not await fresh_repository.is_required(
            capability="trusted_cyber",
            api_key_scope="api-key-b",
            aliases=aliases,
        )
        assert (
            await fresh_repository.require(
                capability="trusted_cyber",
                api_key_scope="api-key-a",
                aliases=aliases,
            )
            == marker_hashes
        )
        stored_markers = (await fresh_session.execute(select(CapabilityLineageMarker))).scalars().all()
        assert len(stored_markers) == 2


@pytest.mark.asyncio
async def test_capability_lineage_read_snapshot_can_upgrade_after_concurrent_writer(db_setup) -> None:
    first_alias = CapabilityLineageAlias(kind="session_header", value="first-session")
    concurrent_alias = CapabilityLineageAlias(kind="session_header", value="concurrent-session")

    async with SessionLocal() as first_session, SessionLocal() as concurrent_session:
        first_repository = CapabilityLineageRepository(first_session)
        concurrent_repository = CapabilityLineageRepository(concurrent_session)

        assert not await first_repository.is_required(
            capability="trusted_cyber",
            api_key_scope="api-key-a",
            aliases=(first_alias,),
        )
        await concurrent_repository.require(
            capability="trusted_cyber",
            api_key_scope="api-key-a",
            aliases=(concurrent_alias,),
        )

        assert await first_repository.require(
            capability="trusted_cyber",
            api_key_scope="api-key-a",
            aliases=(first_alias,),
        )


@pytest.mark.asyncio
async def test_capability_lineage_writes_markers_in_global_hash_order(monkeypatch) -> None:
    class _RecordingSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement: Insert) -> None:
            self.statements.append(cast(str, statement))

        async def commit(self) -> None:
            return None

    aliases = tuple(
        sorted(
            (
                CapabilityLineageAlias(kind="session_header", value="session-a"),
                CapabilityLineageAlias(kind="turn_state", value="turn-a"),
                CapabilityLineageAlias(kind="previous_response", value="response-a"),
            ),
            key=lambda alias: capability_lineage_marker_hash(
                capability="trusted_cyber",
                api_key_scope="api-key-a",
                alias=alias,
            ),
            reverse=True,
        )
    )
    session = _RecordingSession()
    repository = CapabilityLineageRepository(cast(AsyncSession, session))
    monkeypatch.setattr(
        repository,
        "_upsert_statement",
        lambda marker_hash: cast(Insert, marker_hash),
    )

    marker_hashes = await repository.require(
        capability="trusted_cyber",
        api_key_scope="api-key-a",
        aliases=aliases,
    )

    assert marker_hashes == tuple(sorted(marker_hashes, reverse=True))
    assert session.statements == sorted(marker_hashes)
