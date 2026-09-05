from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyLimit,
    ApiKeyUsageReservation,
    LimitType,
    LimitWindow,
    QuotaPlannerDecision,
    QuotaWindowObservation,
    RequestLog,
    UsageHistory,
)
from app.db.session import SessionLocal
from app.modules.api_keys.service import ApiKeyInvalidError, ApiKeyNotFoundError, ApiKeyRateLimitExceededError
from app.modules.quota_planner.logic import PlannerAction, PlannerSettings
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.quota_planner.scheduler import QuotaPlannerScheduler
from app.modules.quota_planner.warmup import QuotaWarmupService, WarmupUsage

pytestmark = pytest.mark.integration

_AUTO_WARMUP_SETTINGS = PlannerSettings(
    mode="auto",
    max_warmup_credits_per_day=1.0,
    allow_synthetic_traffic=True,
    warmup_model_preference="gpt-5.4-mini",
    dry_run=False,
)


def _warmup_test_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.test",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


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
async def test_quota_planner_repository_persists_naive_utc_decision_datetimes(monkeypatch, db_setup):
    del db_setup
    aware_scheduled = datetime(2026, 5, 18, 13, 0, tzinfo=timezone.utc)
    aware_executed = datetime(2026, 5, 18, 13, 30, tzinfo=timezone.utc)

    # SQLite's aiosqlite driver silently strips tzinfo on read, so we inspect the
    # exact values the repository binds to the timezone-naive columns. asyncpg/Postgres
    # raises "can't subtract offset-naive and offset-aware datetimes" if these are aware,
    # so the repository MUST sanitize them before persistence.
    bound_scheduled: list[object] = []
    original_insert_scalar = AsyncSession.scalar

    async def capture_insert_scalar(self, statement, *args, **kwargs):
        values = getattr(statement, "_values", None)
        if values:
            for col, bind in values.items():
                if getattr(col, "name", None) == "scheduled_at":
                    bound_scheduled.append(getattr(bind, "value", bind))
        return await original_insert_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", capture_insert_scalar)

    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        decision = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="aware-datetime-persistence",
            account_id=None,
            scheduled_at=aware_scheduled,
            score=1.0,
            reason="aware",
            status="planned",
        )

    assert bound_scheduled, "log_decision should bind scheduled_at on its insert"
    bound_value = bound_scheduled[-1]
    assert isinstance(bound_value, datetime)
    # Guards the Postgres path: timezone-naive columns must not receive aware datetimes.
    assert bound_value.tzinfo is None
    # The absolute instant must be preserved (converted to UTC).
    assert bound_value == aware_scheduled.replace(tzinfo=None)

    monkeypatch.undo()

    # update_decision_status binds executed_at via an UPDATE ... values() statement.
    # Capture the bound value from the compiled statement parameters.
    bound_executed: list[object] = []
    original_scalar = AsyncSession.scalar

    async def capture_scalar(self, statement, *args, **kwargs):
        values = getattr(statement, "_values", None)
        if values:
            for col, bind in values.items():
                if getattr(col, "name", None) == "executed_at":
                    bound_executed.append(getattr(bind, "value", bind))
        return await original_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", capture_scalar)

    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        updated = await repo.update_decision_status(
            decision.id,
            status="executed",
            executed_at=aware_executed,
        )
        assert updated is not None

    assert bound_executed, "update_decision_status should bind executed_at"
    update_value = bound_executed[-1]
    assert isinstance(update_value, datetime)
    assert update_value.tzinfo is None
    assert update_value == aware_executed.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_quota_planner_repository_preserves_naive_decision_datetimes(db_setup):
    del db_setup
    naive_scheduled = datetime(2026, 5, 18, 9, 0)
    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        decision = await repo.log_decision(
            mode="shadow",
            action="reserve",
            idempotency_key="naive-datetime-persistence",
            account_id=None,
            scheduled_at=naive_scheduled,
            score=1.0,
            reason="naive",
            status="skipped",
        )

    async with SessionLocal() as session:
        stored = await session.get(QuotaPlannerDecision, decision.id)
        assert stored is not None
        assert stored.scheduled_at is not None
        assert stored.scheduled_at == naive_scheduled
        assert stored.scheduled_at.tzinfo is None


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
async def test_quota_planner_warm_now_refuses_weekly_only_account(async_client, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-weekly-only",
            email="weekly-only@example.test",
            plan_type="free",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        # Weekly-only plans surface the weekly window in the primary slot:
        # positive evidence that there is no short window to pre-start.
        session.add(
            UsageHistory(
                account_id="acc-weekly-only",
                used_percent=20.0,
                recorded_at=utcnow(),
                window="primary",
                reset_at=int(utcnow().replace(tzinfo=timezone.utc).timestamp()) - 60,
                window_minutes=10080,
            )
        )
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

    response = await async_client.post(
        "/api/quota-planner/warm-now",
        json={"accountId": "acc-weekly-only", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_short_window"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "race_point",
    ["post_claim", "post_reservation", "authorization_failed", "authorization_cancelled"],
)
async def test_quota_planner_warm_now_final_usage_authorization_blocks_race(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
    race_point: str,
) -> None:
    del db_setup
    account_id = f"acc-warmup-final-authorization-{race_point}"
    with_reservation = race_point != "post_claim"
    async with SessionLocal() as session:
        account = _warmup_test_account(account_id)
        account.usage_limit_enabled = True
        account.usage_limit_percent = 10.0
        session.add(account)
        session.add(
            UsageHistory(
                account_id=account_id,
                used_percent=5.0,
                recorded_at=utcnow(),
                window="primary",
                reset_at=None,
                window_minutes=300,
            )
        )
        await QuotaPlannerRepository(session).upsert_settings(_AUTO_WARMUP_SETTINGS)
        service = QuotaWarmupService(session)
        released_reservations: list[str] = []
        release_started = asyncio.Event()
        allow_release = asyncio.Event()

        async def reach_limit() -> None:
            async with SessionLocal() as race_session:
                race_session.add(
                    UsageHistory(
                        account_id=account_id,
                        used_percent=10.0,
                        recorded_at=utcnow() + timedelta(seconds=1),
                        window="primary",
                        reset_at=None,
                        window_minutes=300,
                    )
                )
                await race_session.commit()

        if with_reservation:

            class FakeApiKeys:
                async def enforce_limits_for_request(self, *args, **kwargs):
                    del args, kwargs
                    if race_point == "post_reservation":
                        await reach_limit()
                    return SimpleNamespace(reservation_id="reservation-final-authorization")

                async def release_usage_reservation(self, reservation_id: str) -> None:
                    if race_point == "authorization_cancelled":
                        release_started.set()
                        await allow_release.wait()
                    released_reservations.append(reservation_id)

            monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        else:
            claim = service._planner.claim_warmup_decision

            async def claim_then_reach_limit(*args, **kwargs):
                claimed = await claim(*args, **kwargs)
                await reach_limit()
                return claimed

            monkeypatch.setattr(service._planner, "claim_warmup_decision", claim_then_reach_limit)

        if race_point in {"authorization_failed", "authorization_cancelled"}:
            load_fresh_standard_usage = service._load_fresh_standard_usage
            load_count = 0

            async def cancel_final_authorization(account_id: str):
                nonlocal load_count
                load_count += 1
                if load_count == 2:
                    if race_point == "authorization_cancelled":
                        raise asyncio.CancelledError()
                    if race_point == "authorization_failed":
                        raise RuntimeError("usage authorization failed")
                return await load_fresh_standard_usage(account_id)

            monkeypatch.setattr(service, "_load_fresh_standard_usage", cancel_final_authorization)

        async def fail_send(*args, **kwargs):
            del args, kwargs
            raise AssertionError("final usage authorization must block the warmup probe")

        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        if race_point == "authorization_cancelled":
            warmup = asyncio.create_task(
                service.warm_now(
                    account_id=account_id,
                    model="gpt-5.4-mini",
                    api_key_id="api-key-race",
                    force_probe=True,
                )
            )
            await release_started.wait()
            warmup.cancel()
            await asyncio.sleep(0)
            warmup.cancel()
            allow_release.set()
            with pytest.raises(asyncio.CancelledError):
                await warmup
            stored = await session.scalar(
                select(QuotaPlannerDecision).where(QuotaPlannerDecision.account_id == account_id)
            )
            assert stored is not None
            decision_id = stored.id
        else:
            result = await service.warm_now(
                account_id=account_id,
                model="gpt-5.4-mini",
                api_key_id="api-key-race" if with_reservation else None,
                force_probe=True,
            )
            decision_id = result.decision_id
        repeated = await service.warm_now(
            account_id=account_id,
            model="gpt-5.4-mini",
            api_key_id="api-key-race" if with_reservation else None,
            force_probe=True,
            decision_id=decision_id,
        )
        stored = await session.scalar(
            select(QuotaPlannerDecision)
            .where(QuotaPlannerDecision.id == decision_id)
            .execution_options(populate_existing=True)
        )

    assert stored is not None
    assert stored.status == "skipped"
    expected_reason = (
        "account_usage_limit_reached"
        if race_point in {"post_claim", "post_reservation"}
        else (
            "account_usage_limit_authorization_cancelled"
            if race_point == "authorization_cancelled"
            else "account_usage_limit_authorization_failed"
        )
    )
    assert stored.reason == expected_reason
    assert repeated.status == "skipped"
    assert repeated.reason == expected_reason
    assert released_reservations == (["reservation-final-authorization"] if with_reservation else [])


@pytest.mark.asyncio
async def test_quota_planner_warm_now_final_authorization_blocks_paused_account(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
) -> None:
    del db_setup
    account_id = "acc-warmup-final-authorization-paused"
    released_reservations: list[str] = []
    async with SessionLocal() as session:
        session.add(_warmup_test_account(account_id))
        await QuotaPlannerRepository(session).upsert_settings(_AUTO_WARMUP_SETTINGS)
        service = QuotaWarmupService(session)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                async with SessionLocal() as race_session:
                    fresh_account = await race_session.get(Account, account_id)
                    assert fresh_account is not None
                    assert fresh_account.status == AccountStatus.ACTIVE
                    fresh_account.status = AccountStatus.PAUSED
                    await race_session.commit()
                return SimpleNamespace(reservation_id="reservation-paused-account")

            async def release_usage_reservation(self, reservation_id: str) -> None:
                released_reservations.append(reservation_id)

        async def fail_send(*args, **kwargs):
            del args, kwargs
            raise AssertionError("final account authorization must block the warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account_id,
            model="gpt-5.4-mini",
            api_key_id="api-key-race",
            force_probe=True,
        )
        stored = await session.scalar(
            select(QuotaPlannerDecision)
            .where(QuotaPlannerDecision.id == result.decision_id)
            .execution_options(populate_existing=True)
        )

    assert stored is not None
    assert stored.status == "skipped"
    assert stored.reason == "account_status_paused"
    assert result.status == "skipped"
    assert result.reason == "account_status_paused"
    assert released_reservations == ["reservation-paused-account"]


@pytest.mark.asyncio
async def test_quota_planner_warm_now_final_authorization_blocks_unavailable_usage_data(
    monkeypatch: pytest.MonkeyPatch,
    db_setup,
) -> None:
    del db_setup
    account_id = "acc-warmup-final-authorization-data-unavailable"
    released_reservations: list[str] = []
    async with SessionLocal() as session:
        account = _warmup_test_account(account_id)
        account.usage_limit_enabled = True
        account.usage_limit_percent = 10.0
        session.add(account)
        session.add(
            UsageHistory(
                account_id=account_id,
                used_percent=5.0,
                recorded_at=utcnow(),
                window="primary",
                reset_at=None,
                window_minutes=300,
            )
        )
        await QuotaPlannerRepository(session).upsert_settings(_AUTO_WARMUP_SETTINGS)
        service = QuotaWarmupService(session)

        async def drop_measurements_after_claim(*args, **kwargs):
            claimed = await claim(*args, **kwargs)
            async with SessionLocal() as race_session:
                await race_session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
                await race_session.commit()
            return claimed

        claim = service._planner.claim_warmup_decision
        monkeypatch.setattr(service._planner, "claim_warmup_decision", drop_measurements_after_claim)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                return SimpleNamespace(reservation_id="reservation-data-unavailable")

            async def release_usage_reservation(self, reservation_id: str) -> None:
                released_reservations.append(reservation_id)

        async def fail_send(*args, **kwargs):
            del args, kwargs
            raise AssertionError("final usage authorization must block the warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account_id,
            model="gpt-5.4-mini",
            api_key_id="api-key-race",
            force_probe=True,
        )
        stored = await session.scalar(
            select(QuotaPlannerDecision)
            .where(QuotaPlannerDecision.id == result.decision_id)
            .execution_options(populate_existing=True)
        )

    assert stored is not None
    assert stored.status == "skipped"
    assert stored.reason == "account_usage_limit_reached"
    assert result.status == "skipped"
    assert result.reason == "account_usage_limit_reached"
    assert released_reservations == ["reservation-data-unavailable"]


@pytest.mark.asyncio
async def test_quota_planner_warm_now_keeps_bootstrap_for_metadata_less_primary_rows(
    monkeypatch, async_client, db_setup
):
    if not hasattr(time, "tzset"):
        pytest.skip("tzset is required to simulate non-UTC local time")

    del db_setup
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    encryptor = TokenEncryptor()
    try:
        now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
        async with SessionLocal() as session:
            account = Account(
                id="acc-metadata-less",
                email="metadata-less@example.test",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
            session.add(account)
            # A legacy primary row without duration metadata plus a newer weekly
            # row: the metadata-less sample keeps the legacy bootstrap path and
            # must not be rejected as a superseded short window.
            session.add(
                UsageHistory(
                    account_id="acc-metadata-less",
                    used_percent=0.0,
                    recorded_at=utcnow() - timedelta(hours=3),
                    window="primary",
                    reset_at=now_epoch - 7200,
                    window_minutes=None,
                )
            )
            session.add(
                UsageHistory(
                    account_id="acc-metadata-less",
                    used_percent=40.0,
                    recorded_at=utcnow(),
                    window="secondary",
                    reset_at=now_epoch + 5 * 24 * 3600,
                    window_minutes=10080,
                )
            )
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
                account_id="acc-metadata-less",
                model="gpt-5.4-mini",
                source="warmup_probe",
                confidence="observed",
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
            json={"accountId": "acc-metadata-less", "model": "gpt-5.4-mini"},
        )
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "executed", payload


@pytest.mark.asyncio
async def test_quota_planner_warm_now_refuses_superseded_short_window(async_client, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        account = Account(
            id="acc-superseded",
            email="superseded@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        # The stale short-window primary row was never rewritten; a later
        # refresh recorded only the weekly row, proving upstream no longer
        # reports the short window.
        session.add(
            UsageHistory(
                account_id="acc-superseded",
                used_percent=40.0,
                recorded_at=utcnow() - timedelta(hours=3),
                window="primary",
                reset_at=now_epoch - 7200,
                window_minutes=300,
            )
        )
        session.add(
            UsageHistory(
                account_id="acc-superseded",
                used_percent=40.0,
                recorded_at=utcnow(),
                window="secondary",
                reset_at=now_epoch + 5 * 24 * 3600,
                window_minutes=10080,
            )
        )
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

    response = await async_client.post(
        "/api/quota-planner/warm-now",
        json={"accountId": "acc-superseded", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_short_window"


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
async def test_quota_planner_scheduler_reclaims_expired_executing_warmup_claim(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    cycle_key = "reclaim-expired-executing-claim"
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-expired-claim",
            email="warm-expired-claim@example.test",
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
                max_warmups_per_day=3,
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
            idempotency_key=f"{cycle_key}:auto:{account.id}:warmup",
            account_id=account.id,
            scheduled_at=utcnow() - timedelta(minutes=5),
            score=3.0,
            reason="expired_claim_reclaim",
            status="planned",
        )
        claimed = await repo.claim_warmup_decision(
            decision.id,
            since=utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
            max_warmups=3,
            max_credits=1.0,
            claim_ttl_seconds=60.0,
        )
        assert claimed is not None
        stale_claim_time = utcnow() - timedelta(minutes=10)
        claimed.executed_at = stale_claim_time
        claimed.lease_expires_at = stale_claim_time
        await session.commit()

    forecast = SimpleNamespace(peak_slot_start=None, peak_demand_units=0.0)
    simulation = SimpleNamespace(loss=0.0, unmet_demand=0.0)

    monkeypatch.setattr("app.modules.quota_planner.scheduler._build_states", lambda **kwargs: ([], {}))
    monkeypatch.setattr("app.modules.quota_planner.scheduler.build_demand_forecast", lambda **kwargs: forecast)
    monkeypatch.setattr("app.modules.quota_planner.scheduler.simulate_pool", lambda **kwargs: simulation)
    monkeypatch.setattr(
        "app.modules.quota_planner.scheduler.plan_shadow_actions",
        lambda **kwargs: [
            PlannerAction(
                account_id="acc-warm-expired-claim",
                action="warmup",
                scheduled_at=utcnow() - timedelta(minutes=1),
                score=3.0,
                reason="expired_claim_reclaim",
                warmup_cycle_key=cycle_key,
            )
        ],
    )

    async def fake_send(self, *, account, model, request_id):
        del self, account, model, request_id
        return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

    async def noop_record_effect(self, account, model, *, source, confidence):
        del self, account, model, source, confidence

    monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
    monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", noop_record_effect)

    await QuotaPlannerScheduler()._run_once_as_leader()

    async with SessionLocal() as session:
        refreshed = await session.get(QuotaPlannerDecision, decision.id)
        assert refreshed is not None
        assert refreshed.status == "executed"
        assert refreshed.reason == "warmup_executed"
        assert refreshed.lease_expires_at is None
        assert refreshed.executed_at is not None
        assert refreshed.executed_at > stale_claim_time
        logs = await session.execute(select(RequestLog).where(RequestLog.request_kind == "warmup"))
        assert len(logs.scalars().all()) == 1


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
async def test_quota_planner_warm_now_limit_free_key_probes_without_reservation(monkeypatch, db_setup):
    """A key with no applicable limits admits without a reservation; the
    warmup probe must execute and never attempt reservation settlement."""
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-unlimited-key",
            email="warm-unlimited-key@example.test",
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
                return None

            async def finalize_usage_reservation(self, *args, **kwargs):
                del args, kwargs
                raise AssertionError("limit-free warmup must not finalize a reservation")

            async def fail_usage_reservation(self, *args, **kwargs):
                del args, kwargs
                raise AssertionError("limit-free warmup must not fail a reservation")

        async def fake_send(self, *, account, model, request_id):
            del self, account, model, request_id
            return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

        async def noop_record_effect(self, account, model, *, source, confidence):
            del self, account, model, source, confidence

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
        monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", noop_record_effect)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-unlimited",
            force_probe=True,
        )

    assert result.status == "executed"
    assert result.reason == "warmup_executed"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_limit_free_admission_keeps_shared_session_state(monkeypatch, db_setup):
    """Regression: the limit-free early return closes the admission
    transaction on the warmup service's shared session. It must do so
    without expiring tracked ORM state (``rollback()`` expires everything
    even with ``expire_on_commit=False``): the probe reads
    ``account.access_token_encrypted`` and the error path reads
    ``decision.id`` after admission, which raised ``MissingGreenlet`` when
    the shared ``account``/``decision`` objects were expired. Exercises the
    REAL ``ApiKeysService`` with a real limit-free key row — a fake api-key
    service returning ``None`` never runs the transaction-closing path."""
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-limit-free-real",
            email="warm-limit-free-real@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        # Real API key with no configured limits: admission takes the
        # limit-free early return inside the real ApiKeysService.
        session.add(
            ApiKey(
                id="api-key-limit-free-real",
                name="limit-free warmup key",
                key_hash="limit-free-warmup-hash",
                key_prefix="sk-lfw",
            )
        )
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
        service = QuotaWarmupService(session)

        probed_tokens: list[str] = []

        async def fake_send(self, *, account, model, request_id):
            del model, request_id
            # Mirror the real probe's first attribute access on the shared
            # session's tracked account: raises MissingGreenlet if admission
            # expired it.
            probed_tokens.append(self._encryptor.decrypt(account.access_token_encrypted))
            return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

        async def noop_record_effect(self, account, model, *, source, confidence):
            del self, account, model, source, confidence

        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
        monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", noop_record_effect)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-limit-free-real",
            force_probe=True,
        )

        # The tracked objects must remain readable after admission (the
        # failure-handling path reads ``decision.id``-style attributes too).
        assert account.access_token_encrypted is not None

    assert probed_tokens == ["access"]
    assert result.status == "executed"
    assert result.reason == "warmup_executed"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "database_error"])
async def test_real_sql_authorization_interruption_releases_warmup_claim_and_reservation(
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A real driver interruption poisons a transaction; a raised mock does not."""
    account_id, key_id = "sql-interruption-owner", "sql-interruption-key"
    sql_started = asyncio.Event()
    release_sql = threading.Event()
    loop = asyncio.get_running_loop()
    send_calls: list[str] = []
    async with SessionLocal() as session:
        account = _warmup_test_account(account_id)
        account.usage_limit_enabled = True
        account.usage_limit_percent = 10
        session.add_all(
            [
                account,
                UsageHistory(
                    account_id=account_id, used_percent=5, window="primary", window_minutes=300, recorded_at=utcnow()
                ),
                ApiKey(id=key_id, name="SQL cancellation test", key_hash="sql-interruption-hash", key_prefix="sk-test"),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.DAILY,
                    max_value=100_000,
                    current_value=0,
                    reset_at=utcnow() + timedelta(days=1),
                ),
            ]
        )
        await QuotaPlannerRepository(session).upsert_settings(_AUTO_WARMUP_SETTINGS)
        service = QuotaWarmupService(session)
        original = service._usage.account_usage_limit_snapshot
        reads = 0
        dialect = session.get_bind().dialect.name

        def sqlite_wait() -> int:
            loop.call_soon_threadsafe(sql_started.set)
            return int(release_sql.wait(timeout=10))

        async def interrupt_final_read(owner_id: str):
            nonlocal reads
            reads += 1
            if reads == 2:
                if failure == "database_error":
                    await session.execute(text("SELECT * FROM pr1528_missing_authorization_table"))
                elif dialect == "sqlite":
                    connection = await session.connection()

                    def register_wait(conn):
                        dbapi = conn.connection.dbapi_connection
                        assert dbapi is not None
                        dbapi.create_function("pr1528_wait", 0, sqlite_wait)

                    await connection.run_sync(register_wait)
                    await session.execute(text("SELECT pr1528_wait()"))
                else:
                    sql_started.set()
                    await session.execute(text("SELECT pg_sleep(30) /* pr1528-authorization-cancel */"))
            return await original(owner_id)

        async def send_probe(**kwargs):
            send_calls.append(kwargs["account"].id)
            raise AssertionError("authorization failure must not dispatch")

        monkeypatch.setattr(service._usage, "account_usage_limit_snapshot", interrupt_final_read)
        monkeypatch.setattr(service, "_send_warmup_probe", send_probe)
        task = asyncio.create_task(
            service.warm_now(
                account_id=account_id,
                model="gpt-5.4-mini",
                api_key_id=key_id,
                force_probe=True,
            )
        )
        try:
            if failure == "cancel":
                await asyncio.wait_for(sql_started.wait(), timeout=5)
                if dialect == "postgresql":

                    async def wait_for_server_query() -> None:
                        async with SessionLocal() as monitor:
                            while not await monitor.scalar(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event = 'PgSleep' "
                                    "AND query LIKE 'SELECT pg_sleep(30)%pr1528-authorization-cancel%')"
                                )
                            ):
                                await monitor.rollback()
                                await asyncio.sleep(0.01)

                    await asyncio.wait_for(wait_for_server_query(), timeout=5)
                async with SessionLocal() as monitor:
                    pending = await monitor.scalar(
                        select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key_id)
                    )
                    assert pending is not None and pending.status == "reserved"
                task.cancel()
                release_sql.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
            else:
                result = await asyncio.wait_for(task, timeout=5)
                assert result.status == "skipped"
                assert result.reason == "account_usage_limit_authorization_failed"
        finally:
            release_sql.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert reads == 2
    assert send_calls == []
    async with SessionLocal() as verification:
        decision = await verification.scalar(
            select(QuotaPlannerDecision).where(QuotaPlannerDecision.account_id == account_id)
        )
        reservation = await verification.scalar(
            select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key_id)
        )
        limit = await verification.scalar(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id))
        assert decision is not None and decision.status == "skipped"
        assert decision.reason == (
            "account_usage_limit_authorization_cancelled"
            if failure == "cancel"
            else "account_usage_limit_authorization_failed"
        )
        assert reservation is not None and reservation.status == "released"
        assert limit is not None and limit.current_value == 0
