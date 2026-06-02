from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, QuotaPlannerDecision, QuotaWindowObservation, RequestLog, UsageHistory
from app.db.session import SessionLocal
from app.modules.api_keys.service import ApiKeyInvalidError, ApiKeyNotFoundError, ApiKeyRateLimitExceededError
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
            state_before_json=(
                '{"target_peak_at":"2026-05-18T13:00:00+00:00",'
                '"expected_gain":9.5,"expected_cost":1.0,"warmup_cycle":"20260518:warmup_cycle:1"}'
            ),
        )

    response = await async_client.get("/api/quota-planner/decisions?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    by_key = {row["idempotencyKey"]: row for row in payload}
    assert by_key["test-decision-new"]["mode"] == "suggest"
    assert by_key["test-decision-new"]["action"] == "warmup"
    assert by_key["test-decision-new"]["reason"] == "new"
    assert by_key["test-decision-new"]["details"]["target_peak_at"] == "2026-05-18T13:00:00+00:00"
    assert by_key["test-decision-new"]["details"]["warmup_cycle"] == "20260518:warmup_cycle:1"


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
async def test_quota_planner_warm_now_does_not_execute_canceled_decision(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-canceled-warm",
            email="canceled-warm@example.test",
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
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
            )
        )
        decision = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="cancelled-auto-warmup",
            account_id=account.id,
            scheduled_at=utcnow(),
            score=3.0,
            reason="operator_review",
            status="planned",
        )
        await repo.update_decision_status(decision.id, status="canceled", reason="admin_canceled")

        async def fail_send(*args, **kwargs):
            del args, kwargs
            raise AssertionError("canceled decision should not execute")

        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await QuotaWarmupService(session).warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            decision_id=decision.id,
        )

    assert result.status == "canceled"
    assert result.reason == "admin_canceled"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_claims_planned_decision_before_probe(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-claim",
            email="warm-claim@example.test",
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
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        decision = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="claim-before-probe",
            account_id=account.id,
            scheduled_at=utcnow(),
            score=3.0,
            reason="operator_review",
            status="planned",
        )
        seen_statuses: list[str] = []

        async def fake_send(self, *, account, model, request_id):
            del self, account, model, request_id
            async with SessionLocal() as observe_session:
                status = await observe_session.scalar(
                    select(QuotaPlannerDecision.status).where(QuotaPlannerDecision.id == decision.id)
                )
                assert status is not None
                seen_statuses.append(status)
            return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

        async def noop_record_effect(self, account, model, *, source, confidence):
            del self, account, model, source, confidence

        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
        monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", noop_record_effect)

        result = await QuotaWarmupService(session).warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            decision_id=decision.id,
        )

    assert seen_statuses == ["executing"]
    assert result.status == "executed"


@pytest.mark.asyncio
async def test_quota_planner_cancel_decision_does_not_cancel_executing(async_client, db_setup):
    del db_setup
    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        decision = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="executing-not-cancelable",
            scheduled_at=utcnow(),
            score=3.0,
            reason="warmup_executing",
            status="executing",
        )

    response = await async_client.post(f"/api/quota-planner/decisions/{decision.id}/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decisionId"] == decision.id
    assert payload["status"] == "executing"
    assert payload["reason"] == "not_cancelable"
    async with SessionLocal() as session:
        status = await session.scalar(select(QuotaPlannerDecision.status).where(QuotaPlannerDecision.id == decision.id))
    assert status == "executing"


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


@pytest.mark.asyncio
async def test_quota_planner_warm_now_ignores_failed_effect_after_prior_observed(monkeypatch, async_client, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-after-failure",
            email="warm-after-failure@example.test",
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
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
            observed_at=utcnow() - timedelta(minutes=10),
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="failed",
            observed_at=utcnow(),
        )

    async def fake_send(self, *, account, model, request_id):
        del self, account, model, request_id
        return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

    async def noop_record_effect(self, account, model, *, source, confidence):
        del self, account, model, source, confidence

    monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
    monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", noop_record_effect)

    response = await async_client.post(
        "/api/quota-planner/warm-now",
        json={"accountId": "acc-warm-after-failure", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "executed"


@pytest.mark.asyncio
async def test_quota_planner_warmup_effect_without_usage_row_is_not_observed(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-no-usage-row",
            email="warm-no-usage-row@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)

        async def refresh_without_usage_row(self, accounts, latest_before_by_account):
            del self, accounts, latest_before_by_account

        monkeypatch.setattr("app.modules.quota_planner.warmup.UsageUpdater.refresh_accounts", refresh_without_usage_row)

        await QuotaWarmupService(session)._record_warmup_effect(
            account,
            "gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )

        result = await session.execute(
            select(QuotaWindowObservation).where(QuotaWindowObservation.account_id == account.id)
        )
        observation = result.scalar_one()

    assert observation.confidence == "unknown"
    assert observation.primary_remaining_percent is None
    assert observation.primary_reset_at is None


@pytest.mark.asyncio
async def test_quota_planner_warmup_effect_with_only_stale_usage_is_not_observed(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-stale-usage-row",
            email="warm-stale-usage-row@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        session.add(
            UsageHistory(
                account_id=account.id,
                used_percent=10.0,
                reset_at=1234,
                window="primary",
                recorded_at=utcnow() - timedelta(minutes=30),
            )
        )
        await session.commit()

        async def refresh_without_new_usage_row(self, accounts, latest_before_by_account):
            del self, accounts, latest_before_by_account

        monkeypatch.setattr(
            "app.modules.quota_planner.warmup.UsageUpdater.refresh_accounts",
            refresh_without_new_usage_row,
        )

        await QuotaWarmupService(session)._record_warmup_effect(
            account,
            "gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )

        result = await session.execute(
            select(QuotaWindowObservation).where(QuotaWindowObservation.account_id == account.id)
        )
        observation = result.scalar_one()

    assert observation.confidence == "unknown"
    assert observation.primary_remaining_percent is None
    assert observation.primary_reset_at is None


@pytest.mark.asyncio
async def test_quota_planner_warm_now_cancellation_releases_api_key_reservation(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-cancel-reservation",
            email="warm-cancel-reservation@example.test",
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
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)
        failed_reservations: list[tuple[str, str, int | None, int | None, int | None]] = []

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                return SimpleNamespace(reservation_id="reservation-cancelled")

            async def fail_usage_reservation(
                self,
                reservation_id,
                *,
                model,
                input_tokens=None,
                output_tokens=None,
                cached_input_tokens=None,
            ):
                failed_reservations.append((reservation_id, model, input_tokens, output_tokens, cached_input_tokens))

        async def cancel_probe(self, *, account, model, request_id):
            del self, account, model, request_id
            raise asyncio.CancelledError()

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", cancel_probe)

        with pytest.raises(asyncio.CancelledError):
            await service.warm_now(
                account_id=account.id,
                model="gpt-5.4-mini",
                api_key_id="api-key-cancel",
                force_probe=True,
            )

    assert failed_reservations == [("reservation-cancelled", "gpt-5.4-mini", 0, 0, 0)]


@pytest.mark.asyncio
async def test_quota_planner_warm_now_api_key_not_found_is_skipped(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-key-not-found",
            email="warm-key-not-found@example.test",
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
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                raise ApiKeyNotFoundError("API key not found: not-existing")

        async def fail_send(self, *, account, model, request_id):
            del self, account, model, request_id
            raise AssertionError("invalid API key should skip before sending warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-not-found",
            force_probe=True,
        )

    assert result.status == "skipped"
    assert result.reason == "api_key_not_found"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_invalid_api_key_is_skipped(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-key-invalid",
            email="warm-key-invalid@example.test",
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
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                raise ApiKeyInvalidError("API key has expired")

        async def fail_send(self, *, account, model, request_id):
            del self, account, model, request_id
            raise AssertionError("expired API key should skip before sending warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-expired",
            force_probe=True,
        )

    assert result.status == "skipped"
    assert result.reason == "api_key_invalid"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_rate_limited_api_key_is_skipped(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-key-rate-limited",
            email="warm-key-rate-limited@example.test",
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
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                raise ApiKeyRateLimitExceededError(message="Too many requests", reset_at=utcnow())

        async def fail_send(self, *, account, model, request_id):
            del self, account, model, request_id
            raise AssertionError("rate-limited API key should skip before sending warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-rate-limited",
            force_probe=True,
        )

    assert result.status == "skipped"
    assert result.reason.startswith("api_key_rate_limit_exceeded:")
