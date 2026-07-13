"""Multi-replica regression tests for atomic quota/warmup budget claims.

Each test simulates two replicas (or two processes sharing one SQLite file) as
two independent sessions over the shared test database. The process-local
``sqlite_writer_section`` lock is patched to a no-op where the scenario models
separate processes, because separate processes never share that lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountLimitWarmup, AccountStatus, QuotaPlannerDecision, RequestLog
from app.db.session import SessionLocal
from app.modules.limit_warmup import repository as limit_warmup_repository_module
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.quota_planner import repository as quota_planner_repository_module
from app.modules.quota_planner.logic import PlannerSettings
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.quota_planner.warmup import QuotaWarmupService, WarmupUsage

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _noop_writer_section() -> AsyncIterator[None]:
    yield


def _simulate_separate_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the process-local SQLite writer lock shared by both replicas.

    Two real processes each have their own lock, so it provides no cross-process
    serialization; removing it in-test reproduces that topology.
    """
    monkeypatch.setattr(limit_warmup_repository_module, "sqlite_writer_section", _noop_writer_section)
    monkeypatch.setattr(quota_planner_repository_module, "sqlite_writer_section", _noop_writer_section)


def _account(account_id: str) -> Account:
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


def _midnight():
    return utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


async def _seed_planner(*accounts: Account, max_warmups_per_day: int = 1) -> None:
    async with SessionLocal() as session:
        for account in accounts:
            session.add(account)
        await QuotaPlannerRepository(session).upsert_settings(
            PlannerSettings(
                mode="auto",
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmups_per_day=max_warmups_per_day,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )


@pytest.mark.asyncio
async def test_concurrent_warm_now_sends_at_most_one_probe_within_count_budget(monkeypatch, db_setup):
    """Two replicas run warm-now with one warmup left in the daily budget.

    Before this change both replicas' gates read the executed-only count (0),
    both unguarded planned->executing transitions succeeded, and both probes
    were sent, double-spending the budget.
    """
    del db_setup
    await _seed_planner(_account("acc-claim-a"), _account("acc-claim-b"), max_warmups_per_day=1)

    probes: list[str] = []

    async def fake_send(self, *, account, model, request_id):
        del self, model, request_id
        probes.append(account.id)
        return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

    async def noop_record_effect(self, account, model, *, source, confidence):
        del self, account, model, source, confidence

    monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
    monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", noop_record_effect)

    async def replica_warm_now(account_id: str):
        async with SessionLocal() as session:
            return await QuotaWarmupService(session).warm_now(
                account_id=account_id,
                model="gpt-5.4-mini",
                force_probe=True,
            )

    results = await asyncio.gather(replica_warm_now("acc-claim-a"), replica_warm_now("acc-claim-b"))

    assert len(probes) == 1
    statuses = sorted(result.status for result in results)
    assert statuses == ["executed", "skipped"]
    refused = next(result for result in results if result.status == "skipped")
    assert refused.reason == "daily_warmup_count_budget_exhausted"
    async with SessionLocal() as session:
        executed = await session.scalar(
            select(func.count(QuotaPlannerDecision.id)).where(QuotaPlannerDecision.status == "executed")
        )
        executing = await session.scalar(
            select(func.count(QuotaPlannerDecision.id)).where(QuotaPlannerDecision.status == "executing")
        )
    assert (executed, executing) == (1, 0)


@pytest.mark.asyncio
async def test_claim_counts_in_flight_executing_decisions(db_setup):
    """An in-flight executing probe reserves count budget at claim time.

    Before this change budget checks counted only ``status='executed'`` rows,
    so a probe that another replica had claimed but not yet finished was
    invisible and a second probe was admitted past the budget.
    """
    del db_setup
    await _seed_planner(_account("acc-inflight"), max_warmups_per_day=1)
    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="inflight-executing",
            account_id="acc-inflight",
            status="executing",
        )
        planned = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="inflight-planned",
            account_id="acc-inflight",
            status="planned",
        )
        assert await repo.count_active_warmups_since(_midnight()) == 1
        assert await repo.count_executed_warmups_since(_midnight()) == 0

    async with SessionLocal() as replica_session:
        replica_repo = QuotaPlannerRepository(replica_session)
        claimed = await replica_repo.claim_warmup_decision(
            planned.id,
            since=_midnight(),
            max_warmups=1,
            max_credits=1.0,
        )

    assert claimed is None
    async with SessionLocal() as session:
        status = await session.scalar(select(QuotaPlannerDecision.status).where(QuotaPlannerDecision.id == planned.id))
    assert status == "planned"


@pytest.mark.asyncio
async def test_claim_counts_cross_midnight_claim_against_claim_day(db_setup):
    """A decision planned yesterday but claimed today consumes today's budget.

    ``QuotaPlannerScheduler.run_once`` persists future-scheduled decisions, so
    ``created_at`` can precede the daily boundary. Before this fix the
    executing-row budget guard filtered by ``created_at >= since``: a row
    claimed today from a yesterday-created decision escaped today's count and
    a second same-day claim was admitted past ``max_warmups_per_day=1``.
    """
    del db_setup
    await _seed_planner(_account("acc-cross-midnight"), max_warmups_per_day=1)
    midnight = _midnight()
    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        planned_yesterday = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="cross-midnight-yesterday",
            account_id="acc-cross-midnight",
            scheduled_at=midnight + timedelta(minutes=5),
            status="planned",
        )
        planned_today = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="cross-midnight-today",
            account_id="acc-cross-midnight",
            status="planned",
        )
        await session.execute(
            update(QuotaPlannerDecision)
            .where(QuotaPlannerDecision.id == planned_yesterday.id)
            .values(created_at=midnight - timedelta(hours=1))
        )
        await session.commit()

    async with SessionLocal() as session:
        repo = QuotaPlannerRepository(session)
        claimed = await repo.claim_warmup_decision(
            planned_yesterday.id,
            since=midnight,
            max_warmups=1,
            max_credits=1.0,
        )
        assert claimed is not None
        assert claimed.status == "executing"
        assert claimed.executed_at is not None
        assert claimed.executed_at >= midnight
        assert await repo.count_active_warmups_since(midnight) == 1

    async with SessionLocal() as replica_session:
        replica_repo = QuotaPlannerRepository(replica_session)
        refused = await replica_repo.claim_warmup_decision(
            planned_today.id,
            since=midnight,
            max_warmups=1,
            max_credits=1.0,
        )

    assert refused is None
    async with SessionLocal() as session:
        status = await session.scalar(
            select(QuotaPlannerDecision.status).where(QuotaPlannerDecision.id == planned_today.id)
        )
    assert status == "planned"


@pytest.mark.asyncio
async def test_claim_refuses_spent_credit_budget_after_stale_gate_read(monkeypatch, db_setup):
    """The claim itself refuses a spent credit budget even if the gate passed.

    The gate is patched to always allow, simulating a replica whose budget
    pre-check read stale state. Before this change the planned->executing
    transition had no budget guard, so the probe was sent anyway.
    """
    del db_setup
    await _seed_planner(_account("acc-credits"), max_warmups_per_day=5)
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id="acc-credits",
                request_id="warmup-cost-today",
                model="gpt-5.4-mini",
                status="success",
                request_kind="warmup",
                requested_at=utcnow(),
                cost_usd=2.0,
            )
        )
        await session.commit()
        decision = await QuotaPlannerRepository(session).log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="credit-budget-claim",
            account_id="acc-credits",
            status="planned",
        )

    async def gate_always_allows(self, *, settings, account, model, force_probe):
        del self, settings, account, model, force_probe
        return True, "ready"

    async def fail_send(self, *, account, model, request_id):
        del self, account, model, request_id
        raise AssertionError("claim must refuse the spent credit budget before any probe is sent")

    monkeypatch.setattr(QuotaWarmupService, "_execution_gate", gate_always_allows)
    monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

    async with SessionLocal() as session:
        result = await QuotaWarmupService(session).warm_now(
            account_id="acc-credits",
            model="gpt-5.4-mini",
            decision_id=decision.id,
        )

    assert result.status == "skipped"
    assert result.reason == "daily_warmup_credit_budget_exhausted"
    async with SessionLocal() as session:
        status = await session.scalar(select(QuotaPlannerDecision.status).where(QuotaPlannerDecision.id == decision.id))
    assert status == "skipped"


@pytest.mark.asyncio
async def test_concurrent_log_decision_converges_on_idempotency_key(monkeypatch, db_setup):
    """Two replicas logging one idempotency key converge on a single row.

    Before this change ``log_decision`` was SELECT-then-INSERT on the unique
    key: when both replicas passed the SELECT before either inserted, the
    second INSERT raised an unhandled IntegrityError that aborted its
    planning tick.
    """
    del db_setup
    _simulate_separate_processes(monkeypatch)

    async def replica_log():
        async with SessionLocal() as session:
            decision = await QuotaPlannerRepository(session).log_decision(
                mode="auto",
                action="warmup",
                idempotency_key="converging-key",
                status="planned",
                reason="tick",
            )
            return decision.id

    first_id, second_id = await asyncio.gather(replica_log(), replica_log())

    assert first_id == second_id
    async with SessionLocal() as session:
        row_count = await session.scalar(
            select(func.count(QuotaPlannerDecision.id)).where(QuotaPlannerDecision.idempotency_key == "converging-key")
        )
    assert row_count == 1


@pytest.mark.asyncio
async def test_limit_warmup_attempts_dedup_within_tolerance_across_processes(monkeypatch, db_setup):
    """Two processes recording near-duplicate reset candidates keep one attempt.

    Before this change the dedup was SELECT .. FOR UPDATE (a no-op on SQLite)
    plus a per-process lock: two processes sharing one SQLite file could both
    pass the tolerant existence check and insert two near-duplicate attempts
    whose ``reset_at`` drifted within the tolerance window, sending two real
    probes.
    """
    del db_setup
    _simulate_separate_processes(monkeypatch)
    async with SessionLocal() as session:
        session.add(_account("acc-limit-warm"))
        await session.commit()

    reset_at = int(utcnow().timestamp()) + 600

    async def replica_attempt(observed_reset_at: int):
        async with SessionLocal() as session:
            return await LimitWarmupRepository(session).try_create_attempt(
                account_id="acc-limit-warm",
                window="primary",
                reset_at=observed_reset_at,
                model="gpt-5.4-mini",
                attempted_at=utcnow(),
                reset_at_tolerance_seconds=60,
            )

    results = await asyncio.gather(replica_attempt(reset_at), replica_attempt(reset_at + 2))

    winners = [attempt for attempt in results if attempt is not None]
    assert len(winners) == 1
    assert winners[0].account_id == "acc-limit-warm"
    assert winners[0].status == "pending"
    async with SessionLocal() as session:
        row_count = await session.scalar(
            select(func.count(AccountLimitWarmup.id)).where(AccountLimitWarmup.account_id == "acc-limit-warm")
        )
    assert row_count == 1


@pytest.mark.asyncio
async def test_limit_warmup_attempt_outside_tolerance_still_inserts(db_setup):
    """The atomic guard only blocks near-duplicates inside the tolerance."""
    del db_setup
    async with SessionLocal() as session:
        session.add(_account("acc-limit-far"))
        await session.commit()

    reset_at = int(utcnow().timestamp()) + 600
    async with SessionLocal() as session:
        repo = LimitWarmupRepository(session)
        first = await repo.try_create_attempt(
            account_id="acc-limit-far",
            window="primary",
            reset_at=reset_at,
            model="gpt-5.4-mini",
            attempted_at=utcnow(),
            reset_at_tolerance_seconds=60,
        )
        duplicate = await repo.try_create_attempt(
            account_id="acc-limit-far",
            window="primary",
            reset_at=reset_at + 30,
            model="gpt-5.4-mini",
            attempted_at=utcnow(),
            reset_at_tolerance_seconds=60,
        )
        far = await repo.try_create_attempt(
            account_id="acc-limit-far",
            window="primary",
            reset_at=reset_at + 3600,
            model="gpt-5.4-mini",
            attempted_at=utcnow(),
            reset_at_tolerance_seconds=60,
        )

    assert first is not None
    assert duplicate is None
    assert far is not None
    assert far.id != first.id
