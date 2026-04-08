from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.exc import ResourceClosedError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from app.modules.request_logs.repository import RequestLogsRepository


@pytest.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = async_session_factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_add_log_ignores_closed_transaction(async_session: AsyncSession, monkeypatch) -> None:
    repo = RequestLogsRepository(async_session)

    async def _commit_failure() -> None:
        raise ResourceClosedError("This transaction is closed")

    async def _refresh_failure(_: object) -> None:
        raise AssertionError("refresh should not be called after commit failure")

    monkeypatch.setattr(async_session, "commit", _commit_failure)
    monkeypatch.setattr(async_session, "refresh", _refresh_failure)

    log = await repo.add_log(
        account_id="acc",
        request_id="req",
        model="gpt-5.2",
        input_tokens=1000,
        output_tokens=500,
        latency_ms=1,
        status="success",
        error_code=None,
    )

    assert log.request_id == "req"
    assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_add_log_defaults_chatgpt_provider_fields(async_session: AsyncSession) -> None:
    repo = RequestLogsRepository(async_session)
    log = await repo.add_log(
        account_id="acc-defaults",
        request_id="req-defaults",
        model="gpt-5.2",
        input_tokens=100,
        output_tokens=50,
        latency_ms=1,
        status="success",
        error_code=None,
    )

    assert log.provider_kind == "chatgpt_web"
    assert log.routing_subject_id == "acc-defaults"
