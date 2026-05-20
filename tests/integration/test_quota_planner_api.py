from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.quota_planner.logic import PlannerSettings
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.quota_planner.warmup import QuotaWarmupService, WarmupUsage

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_quota_planner_settings_api_get_and_update(monkeypatch, async_client, db_setup):
    del db_setup
    monkeypatch.setattr("app.modules.quota_planner.api.AuditService.log_async", lambda *args, **kwargs: None)

    response = await async_client.get("/api/quota-planner/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "shadow"
    assert payload["workingDays"] == [0, 1, 2, 3, 4]
    assert payload["prewarmEnabled"] is True
    assert payload["allowSyntheticTraffic"] is False
    assert payload["dryRun"] is True

    response = await async_client.put(
        "/api/quota-planner/settings",
        json={
            "mode": "shadow",
            "timezone": "Asia/Tbilisi",
            "workingDays": [0, 1, 2, 3, 4, 5],
            "workingHoursStart": "10:00",
            "workingHoursEnd": "19:00",
            "prewarmEnabled": True,
            "prewarmLeadMinutes": 300,
            "maxWarmupsPerDay": 3,
            "maxWarmupCreditsPerDay": 1.5,
            "minExpectedGain": 2.0,
            "forecastQuantile": "p90",
            "allowSyntheticTraffic": False,
            "warmupModelPreference": "gpt-5.4-mini",
            "dryRun": True,
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["mode"] == "shadow"
    assert updated["timezone"] == "Asia/Tbilisi"
    assert updated["workingDays"] == [0, 1, 2, 3, 4, 5]
    assert updated["workingHoursStart"] == "10:00"
    assert updated["maxWarmupsPerDay"] == 3
    assert updated["forecastQuantile"] == "p90"
    assert updated["warmupModelPreference"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_quota_planner_decisions_api_returns_recent_decisions(async_client, db_setup):
    del db_setup
    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        await repo.log_decision(
            mode="shadow",
            action="reserve",
            idempotency_key="test-decision-old",
            account_id=None,
            scheduled_at=utcnow() - timedelta(minutes=10),
            score=1.0,
            reason="old",
            status="skipped",
        )
        await repo.log_decision(
            mode="suggest",
            action="warmup",
            idempotency_key="test-decision-new",
            account_id=None,
            scheduled_at=utcnow(),
            score=5.0,
            reason="new",
            status="planned",
        )

    response = await async_client.get("/api/quota-planner/decisions?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    by_key = {row["idempotencyKey"]: row for row in payload}
    assert by_key["test-decision-new"]["mode"] == "suggest"
    assert by_key["test-decision-new"]["action"] == "warmup"
    assert by_key["test-decision-new"]["reason"] == "new"


@pytest.mark.asyncio
async def test_quota_planner_forecast_api_returns_simulation(async_client, db_setup):
    del db_setup

    response = await async_client.get("/api/quota-planner/forecast?horizonHours=6")

    assert response.status_code == 200
    payload = response.json()
    assert payload["horizonHours"] == 6
    assert payload["slotSeconds"] == 900
    assert "simulation" in payload
    assert payload["simulation"]["forecastUnits"] == payload["totalDemandUnits"]


@pytest.mark.asyncio
async def test_quota_planner_warm_now_defaults_to_safe_skip(async_client, db_setup):
    del db_setup

    response = await async_client.post(
        "/api/quota-planner/warm-now",
        json={"accountId": "acc-missing", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["reason"] == "account_not_found"


@pytest.mark.asyncio
async def test_quota_planner_cancel_decision(async_client, db_setup):
    del db_setup
    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        decision = await repo.log_decision(
            mode="suggest",
            action="warmup",
            idempotency_key="cancel-me",
            account_id=None,
            scheduled_at=utcnow(),
            score=3.0,
            reason="operator_review",
            status="planned",
        )

    response = await async_client.post(f"/api/quota-planner/decisions/{decision.id}/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decisionId"] == decision.id
    assert payload["status"] == "canceled"
    assert payload["reason"] == "admin_canceled"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_executes_when_explicitly_gated(monkeypatch, async_client, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm",
            email="warm@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        repo = QuotaPlannerRepository(session)
        await repo.upsert_settings(
            PlannerSettings(
                mode="auto",
                timezone="UTC",
                working_days=(0, 1, 2, 3, 4),
                working_hours_start="09:00",
                working_hours_end="18:00",
                prewarm_enabled=True,
                prewarm_lead_minutes=300,
                max_warmups_per_day=3,
                max_warmup_credits_per_day=1.0,
                min_expected_gain=1.0,
                forecast_quantile="p75",
                allow_synthetic_traffic=True,
                warmup_model_preference="gpt-5.4-mini",
                dry_run=False,
            )
        )
        await repo.add_window_observation(
            account_id="acc-warm",
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )

    async def fake_send(self, *, account, model, request_id):
        del self, account, model, request_id
        return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

    async def failing_record_effect(self, account, model, *, source, confidence):
        del self, account, model, source, confidence
        raise RuntimeError("usage refresh unavailable")

    monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
    monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", failing_record_effect)

    response = await async_client.post(
        "/api/quota-planner/warm-now",
        json={"accountId": "acc-warm", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "executed"
    async with SessionLocal() as session:
        logs = await session.execute(select(RequestLog).where(RequestLog.request_kind == "warmup"))
        assert logs.scalar_one().request_id == payload["requestId"]
