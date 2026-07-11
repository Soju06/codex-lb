from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _log(request_id: str, *, requested_at) -> RequestLog:
    return RequestLog(
        request_id=request_id,
        model="gpt-5",
        requested_at=requested_at,
        status="success",
    )


@pytest.mark.asyncio
async def test_request_log_retention_hard_deletes_only_expired_rows_and_is_idempotent(db_setup) -> None:
    now = utcnow()
    cutoff = now - timedelta(days=30)
    async with SessionLocal() as session:
        session.add_all(
            [
                _log("expired", requested_at=cutoff - timedelta(seconds=1)),
                _log("at-cutoff", requested_at=cutoff),
                _log("recent", requested_at=now - timedelta(days=1)),
            ]
        )
        await session.commit()

        repository = RequestLogsRepository(session)
        assert await repository.purge_before(cutoff) == 1
        assert await repository.purge_before(cutoff) == 0

        request_ids = list((await session.execute(select(RequestLog.request_id))).scalars().all())
    assert sorted(request_ids) == ["at-cutoff", "recent"]


@pytest.mark.asyncio
async def test_request_log_error_details_can_be_structurally_disabled(db_setup, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_REQUEST_LOG_STORE_ERROR_DETAILS", "false")
    get_settings.cache_clear()
    try:
        async with SessionLocal() as session:
            saved = await RequestLogsRepository(session).add_log(
                account_id=None,
                model="gpt-5",
                input_tokens=None,
                output_tokens=None,
                latency_ms=None,
                status="error",
                request_id="redacted-error",
                error_code="upstream_error",
                error_message="raw upstream response body",
                failure_detail="payload-shaped failure detail",
                failure_exception_type="SensitiveException",
            )
        assert saved.error_code == "upstream_error"
        assert saved.error_message is None
        assert saved.failure_detail is None
        assert saved.failure_exception_type is None
    finally:
        get_settings.cache_clear()
