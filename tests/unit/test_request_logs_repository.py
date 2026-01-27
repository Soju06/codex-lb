from __future__ import annotations

import pytest
from sqlalchemy.exc import ResourceClosedError

from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository


@pytest.mark.asyncio
async def test_add_log_ignores_closed_transaction(monkeypatch) -> None:
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        async def _commit_failure() -> None:
            raise ResourceClosedError("This transaction is closed")

        async def _refresh_failure(_: object) -> None:
            raise AssertionError("refresh should not be called after commit failure")

        monkeypatch.setattr(session, "commit", _commit_failure)
        monkeypatch.setattr(session, "refresh", _refresh_failure)

        log = await repo.add_log(
            account_id="acc",
            request_id="req",
            model="gpt-5.2",
            input_tokens=None,
            output_tokens=None,
            latency_ms=1,
            status="success",
            error_code=None,
        )

        assert log.request_id == "req"
