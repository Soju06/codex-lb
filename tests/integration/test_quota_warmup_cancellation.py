from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.service import ApiKeyRequestUsageBudget
from app.modules.quota_planner.logic import PlannerSettings
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.quota_planner.warmup import QuotaWarmupService, WarmupUsage

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cancelled_warmup_settles_known_usage(
    monkeypatch: pytest.MonkeyPatch,
    db_setup: bool,
) -> None:
    del db_setup
    finalization_started = asyncio.Event()
    allow_finalization = asyncio.Event()
    settlements: list[tuple[str, str, str, int | None, int | None, int | None]] = []

    class FakeApiKeys:
        async def enforce_limits_for_request(
            self,
            api_key_id: str,
            *,
            request_model: str,
            request_usage_budget: ApiKeyRequestUsageBudget,
        ) -> SimpleNamespace:
            del api_key_id, request_model, request_usage_budget
            return SimpleNamespace(reservation_id="reservation-known-usage")

        async def finalize_usage_reservation(
            self,
            reservation_id: str,
            *,
            model: str,
            input_tokens: int,
            output_tokens: int,
            cached_input_tokens: int = 0,
            service_tier: str | None = None,
            cost_microdollars: int | None = None,
        ) -> None:
            del service_tier, cost_microdollars
            finalization_started.set()
            await allow_finalization.wait()
            settlements.append(
                (
                    "finalized",
                    reservation_id,
                    model,
                    input_tokens,
                    output_tokens,
                    cached_input_tokens,
                )
            )

        async def fail_usage_reservation(
            self,
            reservation_id: str,
            *,
            model: str,
            input_tokens: int | None = None,
            output_tokens: int | None = None,
            cached_input_tokens: int | None = None,
            service_tier: str | None = None,
        ) -> None:
            del service_tier
            settlements.append(
                (
                    "failed",
                    reservation_id,
                    model,
                    input_tokens,
                    output_tokens,
                    cached_input_tokens,
                )
            )

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-cancel-known-usage",
            email="warm-cancel-known-usage@example.test",
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
            idempotency_key="cancel-during-known-usage-settlement",
            account_id=account.id,
            scheduled_at=utcnow(),
            score=3.0,
            reason="operator_review",
            status="planned",
        )
        service = QuotaWarmupService(session)

        async def fake_send(
            self: QuotaWarmupService,
            *,
            account: Account,
            model: str,
            request_id: str,
        ) -> WarmupUsage:
            del self, account, model, request_id
            return WarmupUsage(
                input_tokens=7,
                output_tokens=3,
                cached_input_tokens=2,
                reasoning_tokens=1,
            )

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)

        task = asyncio.create_task(
            service.warm_now(
                account_id=account.id,
                model="gpt-5.4-mini",
                api_key_id="api-key-known-usage",
                force_probe=True,
                decision_id=decision.id,
            )
        )
        await asyncio.wait_for(finalization_started.wait(), timeout=1)
        task.cancel("shutdown")
        allow_finalization.set()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

        assert settlements == [
            (
                "finalized",
                "reservation-known-usage",
                "gpt-5.4-mini",
                7,
                3,
                2,
            )
        ]
        refreshed = await repo.get_decision_fresh(decision.id)
        assert refreshed is not None
        assert refreshed.status == "executing"
        request_logs = (
            await session.scalars(select(RequestLog).where(RequestLog.request_id == f"quota-warmup-{decision.id}"))
        ).all()
        assert request_logs == []
