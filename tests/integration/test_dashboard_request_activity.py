from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import AccountUsageRollupState, RequestUsageHourlyRollup
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _epoch(value: datetime) -> int:
    return int(value.replace(tzinfo=timezone.utc).timestamp())


@pytest.mark.asyncio
async def test_request_activity_merges_hourly_rollups_and_bounded_raw_tail(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 8, 10, 12, 34, 56)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    folded_at = datetime(2026, 6, 15, 10, 0, 0)
    tail_at = datetime(2026, 7, 15, 11, 0, 0)

    async with SessionLocal() as session:
        session.add(
            AccountUsageRollupState(
                id=1,
                folded_through=datetime(2026, 8, 1),
                hourly_folded_through=datetime(2026, 7, 1),
                conversation_folded_through=datetime(1970, 1, 1),
            )
        )
        session.add(
            RequestUsageHourlyRollup(
                bucket_epoch=_epoch(folded_at),
                account_id="\x1f",
                api_key_id="\x1f",
                model="gpt-5.2",
                service_tier="\x1f",
                request_kind="normal",
                is_deleted=False,
                request_count=3,
            )
        )
        session.add(
            RequestUsageHourlyRollup(
                bucket_epoch=_epoch(folded_at),
                account_id="account-2",
                api_key_id="api-key-2",
                model="gpt-5.2",
                service_tier="priority",
                request_kind="normal",
                is_deleted=True,
                request_count=4,
            )
        )
        session.add(
            RequestUsageHourlyRollup(
                bucket_epoch=_epoch(folded_at),
                account_id="\x1f",
                api_key_id="\x1f",
                model="gpt-5.2",
                service_tier="\x1f",
                request_kind="warmup",
                is_deleted=False,
                request_count=100,
            )
        )
        await session.commit()

        logs = RequestLogsRepository(session)
        # This row is already represented by the folded count. It must not be
        # read again from request_logs when the watermark is active.
        await logs.add_log(
            account_id=None,
            request_id="folded-request",
            model="gpt-5.2",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=folded_at + timedelta(minutes=5),
        )
        await logs.add_log(
            account_id=None,
            request_id="tail-request",
            model="gpt-5.2",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=tail_at,
        )
        await logs.add_log(
            account_id=None,
            request_id="tail-warmup",
            model="gpt-5.2",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            status="success",
            error_code=None,
            request_kind="warmup",
            requested_at=tail_at + timedelta(minutes=1),
        )
        await logs.add_log(
            account_id=None,
            request_id="outside-window",
            model="gpt-5.2",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=datetime(2026, 2, 1),
        )

    response = await async_client.get("/api/dashboard/request-activity")

    assert response.status_code == 200
    assert response.json() == {
        "days": [
            {"date": "2026-06-15", "requests": 7},
            {"date": "2026-07-15", "requests": 1},
        ]
    }


@pytest.mark.asyncio
async def test_request_activity_uses_local_half_hour_boundaries_and_excludes_after_now(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 8, 10, 0, 30, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)

    async with SessionLocal() as session:
        session.add(
            AccountUsageRollupState(
                id=1,
                folded_through=datetime(2026, 8, 10),
                hourly_folded_through=datetime(2026, 8, 10),
                conversation_folded_through=datetime(1970, 1, 1),
            )
        )
        await session.commit()

        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            request_id="local-day-leading-edge",
            model="gpt-5.2",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=datetime(2026, 8, 9, 18, 45),
        )
        await logs.add_log(
            account_id=None,
            request_id="local-day-tail",
            model="gpt-5.2",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=datetime(2026, 8, 10, 0, 15),
        )
        await logs.add_log(
            account_id=None,
            request_id="after-captured-now",
            model="gpt-5.2",
            input_tokens=0,
            output_tokens=0,
            latency_ms=1,
            status="success",
            error_code=None,
            requested_at=datetime(2026, 8, 10, 0, 31),
        )

    response = await async_client.get("/api/dashboard/request-activity?timezone=Asia%2FKolkata")

    assert response.status_code == 200
    days = response.json()["days"]
    assert {day["date"]: day["requests"] for day in days}["2026-08-10"] == 2
    assert "2026-08-09" not in {day["date"] for day in days}
