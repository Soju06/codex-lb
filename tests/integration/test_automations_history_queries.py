from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Account,
    AccountStatus,
    AutomationJob,
    AutomationRun,
    AutomationRunCycle,
    AutomationRunCycleAccount,
)
from app.db.session import SessionLocal, engine
from app.modules.automations.repository import AutomationsRepository

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class _HistorySeed:
    now: datetime
    job_id: str
    manual_cycle_key: str
    manual_representative_id: str
    scheduled_cycle_keys: tuple[str, str]
    snapshot_only_account_id: str
    observed_account_ids: tuple[str, str]


@contextmanager
def _capture_select_statements() -> Iterator[list[str]]:
    statements: list[str] = []

    def _before_cursor_execute(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)


async def _seed_history() -> _HistorySeed:
    base = datetime(2026, 6, 1, 10, 0, 0)
    now = base + timedelta(days=1)
    account_a = "history-query-a"
    account_b = "history-query-b"
    snapshot_only = "history-query-snapshot-only"
    job_id = "history-query-job"
    manual_cycle = f"manual:{job_id}:manual-cycle"
    scheduled_early = f"scheduled:{job_id}:2026-06-01T10:10:00"
    scheduled_later = f"scheduled:{job_id}:2026-06-01T10:20:00"

    accounts = [
        Account(
            id=account_id,
            chatgpt_account_id=f"chatgpt-{account_id}",
            email=f"{account_id}@example.com",
            plan_type="plus",
            access_token_encrypted=b"access",
            refresh_token_encrypted=b"refresh",
            id_token_encrypted=b"id",
            last_refresh=base,
            status=status,
            deactivation_reason=None,
        )
        for account_id, status in (
            (account_a, AccountStatus.ACTIVE),
            (account_b, AccountStatus.ACTIVE),
            (snapshot_only, AccountStatus.RATE_LIMITED),
        )
    ]
    job = AutomationJob(
        id=job_id,
        name="History query contract",
        enabled=False,
        schedule_type="daily",
        schedule_time="05:00",
        schedule_timezone="UTC",
        schedule_days="mon,tue,wed,thu,fri,sat,sun",
        schedule_threshold_minutes=0,
        include_paused_accounts=False,
        account_scope_all=False,
        model="model-v2",
        reasoning_effort=None,
        prompt="current prompt",
        created_at=base,
        updated_at=now,
    )
    cycles = [
        AutomationRunCycle(
            cycle_key=manual_cycle,
            job_id=job_id,
            trigger="manual",
            cycle_expected_accounts=3,
            cycle_window_end=base + timedelta(minutes=5),
            include_paused_accounts=False,
            created_at=base,
        ),
        AutomationRunCycle(
            cycle_key=scheduled_early,
            job_id=job_id,
            trigger="scheduled",
            cycle_expected_accounts=2,
            cycle_window_end=base + timedelta(minutes=15),
            include_paused_accounts=False,
            created_at=base + timedelta(minutes=10),
        ),
        AutomationRunCycle(
            cycle_key=scheduled_later,
            job_id=job_id,
            trigger="scheduled",
            cycle_expected_accounts=1,
            cycle_window_end=base + timedelta(minutes=25),
            include_paused_accounts=False,
            created_at=base + timedelta(minutes=20),
        ),
    ]
    cycle_accounts = [
        AutomationRunCycleAccount(
            cycle_key=manual_cycle,
            account_id=account_id,
            slot_key=None,
            position=position,
            scheduled_for=base,
        )
        for position, account_id in enumerate((account_a, account_b, snapshot_only))
    ]
    cycle_accounts.extend(
        [
            AutomationRunCycleAccount(
                cycle_key=scheduled_early,
                account_id=account_a,
                slot_key="scheduled-early-a",
                position=0,
                scheduled_for=base + timedelta(minutes=10),
            ),
            AutomationRunCycleAccount(
                cycle_key=scheduled_early,
                account_id=account_b,
                slot_key="scheduled-early-b",
                position=1,
                scheduled_for=base + timedelta(minutes=11),
            ),
            AutomationRunCycleAccount(
                cycle_key=scheduled_later,
                account_id=account_a,
                slot_key="scheduled-later-a",
                position=0,
                scheduled_for=base + timedelta(minutes=20),
            ),
        ]
    )
    runs = [
        AutomationRun(
            id="manual-a-success",
            job_id=job_id,
            trigger="manual",
            slot_key="manual-success-slot",
            cycle_key=manual_cycle,
            cycle_expected_accounts=3,
            cycle_window_end=base + timedelta(minutes=5),
            model="z-model",
            reasoning_effort="high",
            prompt="snapshot prompt",
            scheduled_for=base,
            started_at=base + timedelta(minutes=60),
            finished_at=base + timedelta(minutes=60, seconds=1),
            status="success",
            account_id=account_a,
            error_code=None,
            error_message=None,
            attempt_count=1,
        ),
        AutomationRun(
            id="manual-failed-latest",
            job_id=job_id,
            trigger="manual",
            slot_key="manual-failed-slot",
            cycle_key=manual_cycle,
            cycle_expected_accounts=3,
            cycle_window_end=base + timedelta(minutes=5),
            model="a-model",
            reasoning_effort="medium",
            prompt="snapshot prompt",
            scheduled_for=base + timedelta(seconds=10),
            started_at=base + timedelta(minutes=60),
            finished_at=base + timedelta(minutes=60, seconds=2),
            status="failed",
            account_id=account_b,
            error_code="needle-error",
            error_message="needle failure",
            attempt_count=1,
        ),
        AutomationRun(
            id="scheduled-early-first",
            job_id=job_id,
            trigger="scheduled",
            slot_key="scheduled-early-a",
            cycle_key=scheduled_early,
            cycle_expected_accounts=2,
            cycle_window_end=base + timedelta(minutes=15),
            model="model-v1",
            reasoning_effort=None,
            prompt="snapshot prompt",
            scheduled_for=base + timedelta(minutes=10),
            started_at=base + timedelta(minutes=10),
            finished_at=base + timedelta(minutes=10, seconds=5),
            status="success",
            account_id=account_a,
            error_code=None,
            error_message=None,
            attempt_count=1,
        ),
        AutomationRun(
            id="scheduled-early-late-attempt",
            job_id=job_id,
            trigger="scheduled",
            slot_key="scheduled-early-b",
            cycle_key=scheduled_early,
            cycle_expected_accounts=2,
            cycle_window_end=base + timedelta(minutes=15),
            model="model-v1",
            reasoning_effort=None,
            prompt="snapshot prompt",
            scheduled_for=base + timedelta(minutes=11),
            started_at=base + timedelta(minutes=50),
            finished_at=base + timedelta(minutes=50, seconds=5),
            status="success",
            account_id=account_b,
            error_code=None,
            error_message=None,
            attempt_count=1,
        ),
        AutomationRun(
            id="scheduled-later",
            job_id=job_id,
            trigger="scheduled",
            slot_key="scheduled-later-a",
            cycle_key=scheduled_later,
            cycle_expected_accounts=1,
            cycle_window_end=base + timedelta(minutes=25),
            model="model-v1",
            reasoning_effort=None,
            prompt="snapshot prompt",
            scheduled_for=base + timedelta(minutes=20),
            started_at=base + timedelta(minutes=20),
            finished_at=base + timedelta(minutes=20, seconds=5),
            status="failed",
            account_id=account_a,
            error_code="later-error",
            error_message="later failure",
            attempt_count=1,
        ),
    ]

    async with SessionLocal() as session:
        session.add_all([*accounts, job, *cycles, *cycle_accounts, *runs])
        await session.commit()

    return _HistorySeed(
        now=now,
        job_id=job_id,
        manual_cycle_key=manual_cycle,
        manual_representative_id="manual-failed-latest",
        scheduled_cycle_keys=(scheduled_early, scheduled_later),
        snapshot_only_account_id=snapshot_only,
        observed_account_ids=(account_a, account_b),
    )


async def _seed_paused_policy_cycles(session: AsyncSession, seed: _HistorySeed) -> tuple[str, str]:
    await session.execute(
        update(Account).where(Account.id == seed.snapshot_only_account_id).values(status=AccountStatus.PAUSED)
    )
    due_times = (seed.now - timedelta(hours=2), seed.now - timedelta(hours=1))
    cycle_keys = tuple(f"scheduled:{seed.job_id}:{due.isoformat()}" for due in due_times)
    rows: list[AutomationRunCycle | AutomationRunCycleAccount | AutomationRun] = []
    for position, (cycle_key, due, include_paused, model) in enumerate(
        zip(
            cycle_keys,
            due_times,
            (False, True),
            ("paused-excluded-model", "paused-included-model"),
            strict=True,
        )
    ):
        active_slot = f"paused-policy-{position}-active"
        paused_slot = f"paused-policy-{position}-paused"
        rows.extend(
            [
                AutomationRunCycle(
                    cycle_key=cycle_key,
                    job_id=seed.job_id,
                    trigger="scheduled",
                    cycle_expected_accounts=2,
                    cycle_window_end=due + timedelta(minutes=5),
                    include_paused_accounts=include_paused,
                    created_at=due,
                ),
                AutomationRunCycleAccount(
                    cycle_key=cycle_key,
                    account_id=seed.observed_account_ids[0],
                    slot_key=active_slot,
                    position=0,
                    scheduled_for=due,
                ),
                AutomationRunCycleAccount(
                    cycle_key=cycle_key,
                    account_id=seed.snapshot_only_account_id,
                    slot_key=paused_slot,
                    position=1,
                    scheduled_for=due + timedelta(minutes=1),
                ),
                AutomationRun(
                    id=f"paused-policy-{position}-success",
                    job_id=seed.job_id,
                    trigger="scheduled",
                    slot_key=active_slot,
                    cycle_key=cycle_key,
                    cycle_expected_accounts=2,
                    cycle_window_end=due + timedelta(minutes=5),
                    model=model,
                    reasoning_effort=None,
                    prompt="paused policy snapshot",
                    scheduled_for=due,
                    started_at=due,
                    finished_at=due + timedelta(seconds=5),
                    status="success",
                    account_id=seed.observed_account_ids[0],
                    error_code=None,
                    error_message=None,
                    attempt_count=1,
                ),
            ]
        )
    session.add_all(rows)
    await session.commit()
    return cycle_keys[0], cycle_keys[1]


@pytest.mark.asyncio
async def test_grouped_history_preserves_filters_order_and_bounded_page_queries(db_setup):
    del db_setup
    seed = await _seed_history()

    async with SessionLocal() as session:
        repository = AutomationsRepository(session)
        with _capture_select_statements() as statements:
            rows, total = await repository.list_run_cycles_page(
                limit=25,
                offset=0,
                now_utc=seed.now,
            )

        assert len(statements) == 1
        assert total == 3
        assert [row.cycle_key for row in rows] == [
            seed.scheduled_cycle_keys[1],
            seed.scheduled_cycle_keys[0],
            seed.manual_cycle_key,
        ]
        assert rows[1].id == "scheduled-early-late-attempt"
        assert rows[2].id == seed.manual_representative_id
        assert not re.search(r"\b(?:FROM|JOIN) accounts\b", statements[0], flags=re.IGNORECASE)
        assert "automation_run_cycle_accounts" not in statements[0].lower()

        with _capture_select_statements() as statements:
            empty_rows, empty_total = await repository.list_run_cycles_page(
                limit=25,
                offset=100,
                now_utc=seed.now,
            )
        assert empty_rows == []
        assert empty_total == 3
        assert len(statements) == 2

        search_rows, search_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            search="needle",
        )
        assert search_total == 1
        assert [row.id for row in search_rows] == [seed.manual_representative_id]

        account_rows, account_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            account_ids=[seed.snapshot_only_account_id],
        )
        assert account_total == 1
        assert [row.cycle_key for row in account_rows] == [seed.manual_cycle_key]
        assert account_rows[0].id == seed.manual_representative_id

        snapshot_rows, snapshot_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            models=["model-v1"],
        )
        assert snapshot_total == 2
        assert {row.cycle_key for row in snapshot_rows} == set(seed.scheduled_cycle_keys)

        current_model_rows, current_model_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            models=["model-v2"],
        )
        assert current_model_rows == []
        assert current_model_total == 0

        scheduled_rows, scheduled_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            triggers=["scheduled"],
            job_ids=["history-query-job"],
        )
        assert scheduled_total == 2
        assert {row.cycle_key for row in scheduled_rows} == set(seed.scheduled_cycle_keys)

        missing_rows, missing_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            job_ids=["missing-job"],
        )
        assert missing_rows == []
        assert missing_total == 0

        with _capture_select_statements() as statements:
            partial_rows, partial_total = await repository.list_run_cycles_page(
                limit=25,
                offset=0,
                now_utc=seed.now,
                search="needle",
                statuses=["partial"],
            )
        assert len(statements) == 1
        assert partial_total == 1
        assert [row.cycle_key for row in partial_rows] == [seed.manual_cycle_key]

        paused_excluded_cycle, paused_included_cycle = await _seed_paused_policy_cycles(session, seed)
        excluded_rows, excluded_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            models=["paused-excluded-model"],
            statuses=["success"],
        )
        assert excluded_total == 1
        assert [row.cycle_key for row in excluded_rows] == [paused_excluded_cycle]

        included_rows, included_total = await repository.list_run_cycles_page(
            limit=25,
            offset=0,
            now_utc=seed.now,
            models=["paused-included-model"],
            statuses=["partial"],
        )
        assert included_total == 1
        assert [row.cycle_key for row in included_rows] == [paused_included_cycle]


@pytest.mark.asyncio
async def test_history_option_facets_preserve_status_snapshot_asymmetry_in_one_query(db_setup):
    del db_setup
    seed = await _seed_history()

    async with SessionLocal() as session:
        repository = AutomationsRepository(session)
        with _capture_select_statements() as statements:
            direct_options = await repository.list_run_filter_options(
                now_utc=seed.now,
                account_ids=[seed.snapshot_only_account_id],
            )
        assert len(statements) == 1
        assert direct_options.account_ids == []
        assert direct_options.models == []

        with _capture_select_statements() as statements:
            status_options = await repository.list_run_filter_options(
                now_utc=seed.now,
                account_ids=[seed.snapshot_only_account_id],
                statuses=["partial"],
            )
        assert len(statements) == 1
        assert status_options.account_ids == sorted(seed.observed_account_ids)
        assert seed.snapshot_only_account_id not in status_options.account_ids
        assert status_options.models == ["a-model", "z-model"]


@pytest.mark.asyncio
async def test_history_options_keep_canonical_statuses_and_triggers_with_sparse_history(db_setup, async_client):
    del db_setup
    seed = await _seed_history()
    response = await async_client.get(
        "/api/automations/runs/options",
        params={"accountId": seed.snapshot_only_account_id, "status": "partial"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountIds"] == sorted(seed.observed_account_ids)
    assert payload["models"] == ["a-model", "z-model"]
    assert payload["statuses"] == ["running", "success", "failed", "partial"]
    assert payload["triggers"] == ["scheduled", "manual"]
