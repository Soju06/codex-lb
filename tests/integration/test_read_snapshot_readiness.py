from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.core.types import JsonValue
from app.core.utils.time import utcnow
from app.db.models import ReadSnapshot
from app.db.session import SessionLocal
from app.modules.read_snapshots.keys import resolve_parameters
from app.modules.read_snapshots.repository import SCHEMA_VERSION, SnapshotRepository, encode_parameters

pytestmark = pytest.mark.integration


async def _publish_metadata(zones: tuple[str, ...] = ("UTC",)) -> None:
    async with SessionLocal() as session:
        repository = SnapshotRepository(session)
        keys = [await repository.register("dashboard", {}, pinned=True)]
        for zone in zones:
            if zone != "UTC":
                await repository.register("report-zone", {"timezone": zone})
            for days in (7, 30, 90):
                parameters: dict[str, JsonValue] = {"days": days, "timezone": zone}
                key = await repository.register("report-preset", parameters, pinned=zone == "UTC")
                row = await session.get(ReadSnapshot, key)
                assert row is not None
                resolved = resolve_parameters(parameters)
                row.payload_json = json.dumps(
                    {"range": {name: resolved[name] for name in ("start", "end", "timezone")}}
                )
                await session.commit()
                keys.append(key)
        for key in keys:
            row = await session.get(ReadSnapshot, key)
            assert row is not None
            row.payload_json = row.payload_json or "{}"
            row.generated_at = utcnow()
            row.published_generation = 1
        await session.commit()


@pytest.mark.asyncio
async def test_cold_analytics_readiness_does_not_affect_inference_or_create_interest(async_client, db_setup):
    for _ in range(2):
        response = await async_client.get("/health/read-snapshots")
        assert response.status_code == 503
        assert response.headers["retry-after"] == "2"
        assert (await async_client.get("/health/ready")).status_code == 200
    async with SessionLocal() as session:
        assert (await session.execute(select(ReadSnapshot))).scalars().all() == []


@pytest.mark.asyncio
async def test_shared_ready_values_require_known_zones_with_bounded_metadata_only_reads(async_client, db_setup):
    await _publish_metadata()
    assert (await async_client.get("/health/read-snapshots")).status_code == 200
    async with SessionLocal() as session:
        await SnapshotRepository(session).register("report-zone", {"timezone": "America/New_York"})
    assert (await async_client.get("/health/read-snapshots")).status_code == 503
    await _publish_metadata(("UTC", "America/New_York"))
    statements: list[str] = []
    async with SessionLocal() as session:
        engine = session.get_bind()

        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert await SnapshotRepository(session).ready_for_traffic()
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 2
    assert all(statement.lstrip().startswith("SELECT") for statement in statements)
    assert all("request_logs" not in statement and "usage_history" not in statement for statement in statements)
    assert ", read_snapshots.payload_json," not in statements[-1]
    assert "json" in statements[-1].lower()
    assert (await async_client.get("/health/read-snapshots")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stale", "wrong-schema", "unpublished", "yesterday", "missing-payload"])
async def test_analytics_readiness_refuses_incomplete_or_expired_generations(async_client, db_setup, failure):
    await _publish_metadata()
    key, _ = encode_parameters("report-preset", {"days": 90, "timezone": "UTC"})
    async with SessionLocal() as session:
        row = await session.get(ReadSnapshot, key)
        assert row is not None
        if failure == "stale":
            row.generated_at = utcnow() - timedelta(seconds=301)
        elif failure == "wrong-schema":
            row.schema_version = SCHEMA_VERSION + 1
        elif failure == "unpublished":
            row.published_generation = 0
        elif failure == "yesterday":
            # A fresh completion clock alone does not establish the report day.
            row.generated_at = utcnow()
            row.payload_json = '{"range":{"end":"2000-01-01"}}'
        else:
            row.payload_json = None
        await session.commit()
    assert (await async_client.get("/health/read-snapshots")).status_code == 503
    assert (await async_client.get("/health/ready")).status_code == 200


@pytest.mark.asyncio
async def test_local_midnight_requires_new_preset_even_with_recent_publication(async_client, db_setup, monkeypatch):
    clock = datetime(2026, 10, 19, 3, 59, 59)
    monkeypatch.setattr(__name__ + ".utcnow", lambda: clock)
    monkeypatch.setattr("app.modules.read_snapshots.repository.utcnow", lambda: clock)
    monkeypatch.setattr("app.modules.read_snapshots.keys.utcnow", lambda: clock)
    await _publish_metadata(("UTC", "America/New_York"))
    assert (await async_client.get("/health/read-snapshots")).status_code == 200
    clock += timedelta(seconds=2)
    assert (await async_client.get("/health/read-snapshots")).status_code == 503
    await _publish_metadata(("UTC", "America/New_York"))
    assert (await async_client.get("/health/read-snapshots")).status_code == 200
