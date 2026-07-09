from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha1
from uuid import uuid4

from sqlalchemy import and_, case, delete, exists, func, insert, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AutomationJob,
    AutomationJobAccount,
    AutomationRun,
    AutomationRunCycle,
    AutomationRunCycleAccount,
)

DEFAULT_AUTOMATION_SCHEDULE_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True, slots=True)
class AutomationRunRecord:
    id: str
    job_id: str
    job_name: str | None
    model: str | None
    reasoning_effort: str | None
    prompt: str | None
    trigger: str
    status: str
    slot_key: str
    cycle_key: str
    cycle_expected_accounts: int | None
    cycle_window_end: datetime | None
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime | None
    account_id: str | None
    error_code: str | None
    error_message: str | None
    attempt_count: int


@dataclass(frozen=True, slots=True)
class AutomationScheduledRunClaimRecord:
    run: AutomationRunRecord | None
    snapshot_account_exists: bool


@dataclass(frozen=True, slots=True)
class AutomationJobRecord:
    id: str
    name: str
    enabled: bool
    schedule_type: str
    schedule_time: str
    schedule_timezone: str
    schedule_days: list[str]
    schedule_threshold_minutes: int
    include_paused_accounts: bool
    account_scope_all: bool
    model: str
    reasoning_effort: str | None
    prompt: str
    account_ids: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AutomationRunCycleAccountRecord:
    account_id: str
    slot_key: str | None
    position: int
    scheduled_for: datetime


@dataclass(frozen=True, slots=True)
class AutomationRunCycleRecord:
    cycle_key: str
    job_id: str
    trigger: str
    cycle_expected_accounts: int
    cycle_window_end: datetime | None
    include_paused_accounts: bool
    accounts: list[AutomationRunCycleAccountRecord]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AutomationJobsFilterOptionsRecord:
    account_ids: list[str]
    models: list[str]
    statuses: list[str]
    schedule_types: list[str]


@dataclass(frozen=True, slots=True)
class AutomationRunsFilterOptionsRecord:
    account_ids: list[str]
    models: list[str]
    statuses: list[str]
    triggers: list[str]


class AutomationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_jobs(self) -> list[AutomationJobRecord]:
        result = await self._session.execute(
            select(AutomationJob)
            .options(selectinload(AutomationJob.account_links))
            .order_by(AutomationJob.created_at.desc(), AutomationJob.id.asc())
        )
        jobs = list(result.scalars().all())
        return [self._job_from_model(job) for job in jobs]

    async def list_jobs_page(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        account_ids: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        schedule_types: Sequence[str] | None = None,
    ) -> tuple[list[AutomationJobRecord], int]:
        conditions = self._build_job_conditions(
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=statuses,
            schedule_types=schedule_types,
        )
        stmt = (
            select(AutomationJob)
            .options(selectinload(AutomationJob.account_links))
            .order_by(AutomationJob.created_at.desc(), AutomationJob.id.asc())
            .offset(offset)
            .limit(limit)
        )
        if conditions:
            stmt = stmt.where(and_(*conditions))
        result = await self._session.execute(stmt)
        jobs = [self._job_from_model(job) for job in result.scalars().all()]

        count_stmt = select(func.count(AutomationJob.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total = int((await self._session.execute(count_stmt)).scalar_one() or 0)
        return jobs, total

    async def list_job_filter_options(
        self,
        *,
        search: str | None = None,
        account_ids: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        schedule_types: Sequence[str] | None = None,
    ) -> AutomationJobsFilterOptionsRecord:
        conditions = self._build_job_conditions(
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=statuses,
            schedule_types=schedule_types,
        )

        account_stmt = (
            select(AutomationJobAccount.account_id)
            .distinct()
            .join(AutomationJob, AutomationJob.id == AutomationJobAccount.job_id)
            .order_by(AutomationJobAccount.account_id.asc())
        )
        model_stmt = select(AutomationJob.model).distinct().order_by(AutomationJob.model.asc())
        type_stmt = select(AutomationJob.schedule_type).distinct().order_by(AutomationJob.schedule_type.asc())
        status_stmt = select(AutomationJob.enabled).distinct()
        if conditions:
            clause = and_(*conditions)
            account_stmt = account_stmt.where(clause)
            model_stmt = model_stmt.where(clause)
            type_stmt = type_stmt.where(clause)
            status_stmt = status_stmt.where(clause)

        account_ids_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)
        type_rows = await self._session.execute(type_stmt)
        status_rows = await self._session.execute(status_stmt)

        statuses = sorted({"enabled" if bool(value) else "disabled" for (value,) in status_rows.all()})
        return AutomationJobsFilterOptionsRecord(
            account_ids=[value for (value,) in account_ids_rows.all() if value],
            models=[value for (value,) in model_rows.all() if value],
            statuses=statuses,
            schedule_types=[value for (value,) in type_rows.all() if value],
        )

    async def list_enabled_jobs(self) -> list[AutomationJobRecord]:
        result = await self._session.execute(
            select(AutomationJob)
            .where(AutomationJob.enabled.is_(True))
            .options(selectinload(AutomationJob.account_links))
            .order_by(AutomationJob.created_at.asc(), AutomationJob.id.asc())
        )
        jobs = list(result.scalars().all())
        return [self._job_from_model(job) for job in jobs]

    async def list_due_scheduled_run_cycle_job_ids(
        self,
        *,
        now_utc: datetime,
    ) -> list[str]:
        batch_size = 500
        offset = 0
        due_job_ids: list[str] = []
        known_job_ids: set[str] = set()
        while True:
            candidates = await self._list_due_scheduled_run_cycle_candidates(
                job_id=None,
                limit=batch_size,
                offset=offset,
            )
            if not candidates:
                break

            due_cycles = await self._filter_due_scheduled_run_cycles(candidates, now_utc=now_utc)
            for cycle in due_cycles:
                if cycle.job_id and cycle.job_id not in known_job_ids:
                    known_job_ids.add(cycle.job_id)
                    due_job_ids.append(cycle.job_id)

            if len(candidates) < batch_size:
                break
            offset += batch_size
        return due_job_ids

    async def get_job(self, job_id: str) -> AutomationJobRecord | None:
        result = await self._session.execute(
            select(AutomationJob).where(AutomationJob.id == job_id).options(selectinload(AutomationJob.account_links))
        )
        job = result.scalar_one_or_none()
        if job is None:
            return None
        return self._job_from_model(job)

    async def get_jobs_by_ids(self, job_ids: Sequence[str]) -> dict[str, AutomationJobRecord]:
        normalized_job_ids = [job_id for job_id in job_ids if job_id]
        if not normalized_job_ids:
            return {}
        result = await self._session.execute(
            select(AutomationJob)
            .where(AutomationJob.id.in_(list(dict.fromkeys(normalized_job_ids))))
            .options(selectinload(AutomationJob.account_links))
        )
        jobs = [self._job_from_model(job) for job in result.scalars().all()]
        return {job.id: job for job in jobs}

    async def create_job(
        self,
        *,
        name: str,
        enabled: bool,
        schedule_type: str,
        schedule_time: str,
        schedule_timezone: str,
        schedule_days: Sequence[str],
        schedule_threshold_minutes: int,
        include_paused_accounts: bool,
        model: str,
        reasoning_effort: str | None,
        prompt: str,
        account_ids: Sequence[str],
    ) -> AutomationJobRecord:
        job = AutomationJob(
            id=f"job_{uuid4().hex}",
            name=name,
            enabled=enabled,
            schedule_type=schedule_type,
            schedule_time=schedule_time,
            schedule_timezone=schedule_timezone,
            schedule_days=_serialize_schedule_days(schedule_days),
            schedule_threshold_minutes=schedule_threshold_minutes,
            include_paused_accounts=include_paused_accounts,
            account_scope_all=not bool(account_ids),
            model=model,
            reasoning_effort=reasoning_effort,
            prompt=prompt,
        )
        job.account_links = [
            AutomationJobAccount(job_id=job.id, account_id=account_id, position=index)
            for index, account_id in enumerate(account_ids)
        ]
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)
        await self._session.refresh(job, attribute_names=["account_links"])
        return self._job_from_model(job)

    async def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        schedule_type: str | None = None,
        schedule_time: str | None = None,
        schedule_timezone: str | None = None,
        schedule_days: Sequence[str] | None = None,
        schedule_threshold_minutes: int | None = None,
        include_paused_accounts: bool | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        reasoning_effort_set: bool = False,
        prompt: str | None = None,
        account_ids: Sequence[str] | None = None,
    ) -> AutomationJobRecord | None:
        result = await self._session.execute(
            select(AutomationJob).where(AutomationJob.id == job_id).options(selectinload(AutomationJob.account_links))
        )
        job = result.scalar_one_or_none()
        if job is None:
            return None

        if name is not None:
            job.name = name
        if enabled is not None:
            job.enabled = enabled
        if schedule_type is not None:
            job.schedule_type = schedule_type
        if schedule_time is not None:
            job.schedule_time = schedule_time
        if schedule_timezone is not None:
            job.schedule_timezone = schedule_timezone
        if schedule_days is not None:
            job.schedule_days = _serialize_schedule_days(schedule_days)
        if schedule_threshold_minutes is not None:
            job.schedule_threshold_minutes = schedule_threshold_minutes
        if include_paused_accounts is not None:
            job.include_paused_accounts = include_paused_accounts
        if model is not None:
            job.model = model
        if reasoning_effort_set:
            job.reasoning_effort = reasoning_effort
        if prompt is not None:
            job.prompt = prompt
        if account_ids is not None:
            current_account_ids = [
                link.account_id for link in sorted(job.account_links, key=lambda link: link.position)
            ]
            next_account_ids = list(account_ids)
            job.account_scope_all = not bool(next_account_ids)
            if current_account_ids != next_account_ids:
                job.updated_at = utcnow()
                await self._session.execute(delete(AutomationJobAccount).where(AutomationJobAccount.job_id == job.id))
                await self._session.flush()
                self._session.add_all(
                    [
                        AutomationJobAccount(job_id=job.id, account_id=account_id, position=index)
                        for index, account_id in enumerate(next_account_ids)
                    ]
                )

        await self._session.commit()
        await self._session.refresh(job)
        await self._session.refresh(job, attribute_names=["account_links"])
        return self._job_from_model(job)

    async def delete_job(self, job_id: str) -> bool:
        result = await self._session.execute(
            delete(AutomationJob).where(AutomationJob.id == job_id).returning(AutomationJob.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def list_existing_account_ids(self, account_ids: Sequence[str]) -> set[str]:
        if not account_ids:
            return set()
        result = await self._session.execute(select(Account.id).where(Account.id.in_(list(account_ids))))
        return set(result.scalars().all())

    async def claim_run(
        self,
        *,
        job_id: str,
        trigger: str,
        slot_key: str,
        cycle_key: str,
        cycle_expected_accounts: int | None,
        cycle_window_end: datetime | None,
        scheduled_for: datetime,
        started_at: datetime,
        account_id: str | None = None,
    ) -> AutomationRunRecord | None:
        model, reasoning_effort, prompt = await self._get_job_snapshot(job_id)
        run = AutomationRun(
            id=f"run_{uuid4().hex}",
            job_id=job_id,
            trigger=trigger,
            slot_key=slot_key,
            cycle_key=cycle_key,
            cycle_expected_accounts=cycle_expected_accounts,
            cycle_window_end=cycle_window_end,
            model=model,
            reasoning_effort=reasoning_effort,
            prompt=prompt,
            scheduled_for=scheduled_for,
            started_at=started_at,
            status="running",
            account_id=account_id,
            attempt_count=0,
        )
        self._session.add(run)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            return None
        await self._session.refresh(run)
        return self._run_from_model(run)

    async def claim_scheduled_cycle_account_run(
        self,
        *,
        job_id: str,
        trigger: str,
        slot_key: str,
        cycle_key: str,
        cycle_expected_accounts: int | None,
        cycle_window_end: datetime | None,
        scheduled_for: datetime,
        started_at: datetime,
        account_id: str,
    ) -> AutomationScheduledRunClaimRecord:
        model, reasoning_effort, prompt = await self._get_job_snapshot(job_id)
        run_id = f"run_{uuid4().hex}"
        stmt = (
            insert(AutomationRun)
            .from_select(
                [
                    "id",
                    "job_id",
                    "trigger",
                    "slot_key",
                    "cycle_key",
                    "cycle_expected_accounts",
                    "cycle_window_end",
                    "model",
                    "reasoning_effort",
                    "prompt",
                    "scheduled_for",
                    "started_at",
                    "status",
                    "account_id",
                    "attempt_count",
                ],
                select(
                    literal(run_id),
                    literal(job_id),
                    literal(trigger),
                    literal(slot_key),
                    literal(cycle_key),
                    literal(cycle_expected_accounts),
                    literal(cycle_window_end),
                    literal(model),
                    literal(reasoning_effort),
                    literal(prompt),
                    literal(scheduled_for),
                    literal(started_at),
                    literal("running"),
                    literal(account_id),
                    literal(0),
                ).where(
                    exists(
                        select(AutomationRunCycleAccount.account_id)
                        .where(AutomationRunCycleAccount.cycle_key == cycle_key)
                        .where(AutomationRunCycleAccount.account_id == account_id)
                    )
                ),
            )
            .returning(AutomationRun)
        )
        try:
            result = await self._session.execute(stmt)
            run = result.scalar_one_or_none()
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            snapshot_account_exists = await self.run_cycle_account_exists(cycle_key=cycle_key, account_id=account_id)
            return AutomationScheduledRunClaimRecord(run=None, snapshot_account_exists=snapshot_account_exists)
        if run is None:
            return AutomationScheduledRunClaimRecord(run=None, snapshot_account_exists=False)
        return AutomationScheduledRunClaimRecord(run=self._run_from_model(run), snapshot_account_exists=True)

    async def get_run_cycle(self, *, cycle_key: str) -> AutomationRunCycleRecord | None:
        result = await self._session.execute(
            select(AutomationRunCycle)
            .where(AutomationRunCycle.cycle_key == cycle_key)
            .options(selectinload(AutomationRunCycle.cycle_accounts))
            .execution_options(populate_existing=True)
            .limit(1)
        )
        cycle = result.scalar_one_or_none()
        if cycle is None:
            return None
        return self._run_cycle_from_model(cycle)

    async def list_due_scheduled_run_cycles(
        self,
        *,
        job_id: str,
        now_utc: datetime,
        limit: int = 500,
    ) -> list[AutomationRunCycleRecord]:
        batch_size = max(limit, 500)
        offset = 0
        due_cycles: list[AutomationRunCycleRecord] = []
        while len(due_cycles) < limit:
            candidates = await self._list_due_scheduled_run_cycle_candidates(
                job_id=job_id,
                limit=batch_size,
                offset=offset,
            )
            if not candidates:
                break
            due_cycles.extend(await self._filter_due_scheduled_run_cycles(candidates, now_utc=now_utc))
            if len(candidates) < batch_size:
                break
            offset += batch_size
        return due_cycles[:limit]

    async def _list_due_scheduled_run_cycle_candidates(
        self,
        *,
        job_id: str | None,
        limit: int,
        offset: int,
    ) -> list[AutomationRunCycleRecord]:
        stmt = (
            select(AutomationRunCycle)
            .where(AutomationRunCycle.trigger == "scheduled")
            .options(selectinload(AutomationRunCycle.cycle_accounts))
            .execution_options(populate_existing=True)
            .order_by(AutomationRunCycle.created_at.asc(), AutomationRunCycle.cycle_key.asc())
            .offset(offset)
            .limit(limit)
        )
        if job_id is not None:
            stmt = stmt.where(AutomationRunCycle.job_id == job_id)
        result = await self._session.execute(stmt)
        return [self._run_cycle_from_model(cycle) for cycle in result.scalars().all()]

    async def _filter_due_scheduled_run_cycles(
        self,
        candidates: list[AutomationRunCycleRecord],
        *,
        now_utc: datetime,
    ) -> list[AutomationRunCycleRecord]:
        if not candidates:
            return []
        cycle_keys = [cycle.cycle_key for cycle in candidates]
        stale_started_before = _automation_run_execution_claim_stale_started_before(now_utc)
        result = await self._session.execute(
            select(
                AutomationRun.cycle_key,
                AutomationRun.account_id,
                AutomationRun.slot_key,
                AutomationRun.status,
                AutomationRun.finished_at,
                AutomationRun.started_at,
                AutomationRun.scheduled_for,
            ).where(AutomationRun.cycle_key.in_(cycle_keys))
        )
        occupied_slot_keys_by_cycle_key: dict[str, set[str]] = {}
        for cycle_key, _account_id, slot_key, status, finished_at, started_at, scheduled_for in result.all():
            is_stale_running = (
                status == "running"
                and finished_at is None
                and (started_at <= scheduled_for or started_at < stale_started_before)
            )
            if is_stale_running:
                continue
            occupied_slot_keys_by_cycle_key.setdefault(cycle_key, set()).add(slot_key)

        due_cycles: list[AutomationRunCycleRecord] = []
        for cycle in candidates:
            due_slot = _parse_scheduled_cycle_due_slot(cycle.cycle_key, job_id=cycle.job_id)
            if due_slot is None:
                due_cycles.append(cycle)
                continue
            occupied_slot_keys = occupied_slot_keys_by_cycle_key.get(cycle.cycle_key, set())
            if not cycle.accounts:
                if not occupied_slot_keys and due_slot <= now_utc:
                    due_cycles.append(cycle)
                continue
            has_due_account = False
            for cycle_account in cycle.accounts:
                if cycle_account.scheduled_for > now_utc:
                    continue
                slot_key = _scheduled_slot_key(
                    cycle.job_id,
                    account_id=cycle_account.account_id,
                    due_slot=due_slot,
                )
                if slot_key in occupied_slot_keys:
                    continue
                has_due_account = True
                break
            if has_due_account:
                due_cycles.append(cycle)
        return due_cycles

    async def create_run_cycle(
        self,
        *,
        cycle_key: str,
        job_id: str,
        trigger: str,
        cycle_expected_accounts: int,
        cycle_window_end: datetime | None,
        accounts: Sequence[tuple[str, datetime]],
        include_paused_accounts: bool = False,
    ) -> AutomationRunCycleRecord:
        cycle = AutomationRunCycle(
            cycle_key=cycle_key,
            job_id=job_id,
            trigger=trigger,
            cycle_expected_accounts=cycle_expected_accounts,
            cycle_window_end=cycle_window_end,
            include_paused_accounts=include_paused_accounts,
        )
        due_slot = _parse_scheduled_cycle_due_slot(cycle_key, job_id=job_id) if trigger == "scheduled" else None
        cycle.cycle_accounts = [
            AutomationRunCycleAccount(
                cycle_key=cycle_key,
                account_id=account_id,
                slot_key=_cycle_account_slot_key(
                    job_id=job_id,
                    trigger=trigger,
                    account_id=account_id,
                    due_slot=due_slot,
                ),
                position=index,
                scheduled_for=scheduled_for,
            )
            for index, (account_id, scheduled_for) in enumerate(accounts)
        ]
        self._session.add(cycle)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
        stored_cycle = await self.get_run_cycle(cycle_key=cycle_key)
        if stored_cycle is None:
            raise LookupError(f"Automation run cycle not found: {cycle_key}")
        return stored_cycle

    async def create_run_cycle_with_runs(
        self,
        *,
        cycle_key: str,
        job_id: str,
        trigger: str,
        cycle_expected_accounts: int,
        cycle_window_end: datetime | None,
        accounts: Sequence[tuple[str, datetime]],
        runs: Sequence[tuple[str, datetime, str | None]],
        started_at: datetime,
        include_paused_accounts: bool = False,
    ) -> tuple[AutomationRunCycleRecord, list[AutomationRunRecord]]:
        model, reasoning_effort, prompt = await self._get_job_snapshot(job_id)
        cycle = AutomationRunCycle(
            cycle_key=cycle_key,
            job_id=job_id,
            trigger=trigger,
            cycle_expected_accounts=cycle_expected_accounts,
            cycle_window_end=cycle_window_end,
            include_paused_accounts=include_paused_accounts,
        )
        due_slot = _parse_scheduled_cycle_due_slot(cycle_key, job_id=job_id) if trigger == "scheduled" else None
        cycle.cycle_accounts = [
            AutomationRunCycleAccount(
                cycle_key=cycle_key,
                account_id=account_id,
                slot_key=_cycle_account_slot_key(
                    job_id=job_id,
                    trigger=trigger,
                    account_id=account_id,
                    due_slot=due_slot,
                ),
                position=index,
                scheduled_for=scheduled_for,
            )
            for index, (account_id, scheduled_for) in enumerate(accounts)
        ]
        claimed_runs = [
            AutomationRun(
                id=f"run_{uuid4().hex}",
                job_id=job_id,
                trigger=trigger,
                slot_key=slot_key,
                cycle_key=cycle_key,
                cycle_expected_accounts=cycle_expected_accounts,
                cycle_window_end=cycle_window_end,
                model=model,
                reasoning_effort=reasoning_effort,
                prompt=prompt,
                scheduled_for=scheduled_for,
                started_at=started_at,
                status="running",
                account_id=account_id,
                attempt_count=0,
            )
            for slot_key, scheduled_for, account_id in runs
        ]
        self._session.add(cycle)
        self._session.add_all(claimed_runs)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise
        stored_cycle = await self.get_run_cycle(cycle_key=cycle_key)
        if stored_cycle is None:
            raise LookupError(f"Automation run cycle not found: {cycle_key}")
        return stored_cycle, [self._run_from_model(run) for run in claimed_runs]

    async def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime,
        account_id: str | None,
        error_code: str | None,
        error_message: str | None,
        attempt_count: int,
    ) -> AutomationRunRecord:
        run = await self._session.get(AutomationRun, run_id)
        if run is None:
            raise LookupError(f"Automation run not found: {run_id}")
        run.status = status
        run.finished_at = finished_at
        run.account_id = account_id
        run.error_code = error_code
        run.error_message = error_message
        run.attempt_count = attempt_count
        await self._session.commit()
        await self._session.refresh(run)
        return self._run_from_model(run)

    async def list_runs(self, job_id: str, *, limit: int) -> list[AutomationRunRecord]:
        result = await self._session.execute(
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .where(AutomationRun.job_id == job_id)
            .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
            .limit(limit)
        )
        return [
            self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)
            for run, job_name, model, reasoning_effort in result.all()
        ]

    async def _get_job_snapshot(self, job_id: str) -> tuple[str | None, str | None, str | None]:
        result = await self._session.execute(
            select(AutomationJob.model, AutomationJob.reasoning_effort, AutomationJob.prompt)
            .where(AutomationJob.id == job_id)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None, None, None
        return row[0], row[1], row[2]

    async def get_run(self, run_id: str) -> AutomationRunRecord | None:
        result = await self._session.execute(
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .where(AutomationRun.id == run_id)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        run, job_name, model, reasoning_effort = row
        return self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)

    async def list_runs_for_cycle_key(self, *, cycle_key: str) -> list[AutomationRunRecord]:
        result = await self._session.execute(
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .where(AutomationRun.cycle_key == cycle_key)
            .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
        )
        return [
            self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)
            for run, job_name, model, reasoning_effort in result.all()
        ]

    async def list_runs_for_manual_cycle(
        self,
        *,
        job_id: str,
        slot_key_prefix: str,
    ) -> list[AutomationRunRecord]:
        result = await self._session.execute(
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .where(AutomationRun.job_id == job_id)
            .where(AutomationRun.trigger == "manual")
            .where(AutomationRun.slot_key.like(f"{slot_key_prefix}%"))
            .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
        )
        return [
            self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)
            for run, job_name, model, reasoning_effort in result.all()
        ]

    async def list_due_manual_runs(
        self,
        *,
        now_utc: datetime,
        stale_started_before: datetime,
        cycle_key: str | None = None,
        limit: int = 500,
    ) -> list[AutomationRunRecord]:
        stmt = (
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .outerjoin(
                AutomationRunCycleAccount,
                and_(
                    AutomationRunCycleAccount.cycle_key == AutomationRun.cycle_key,
                    AutomationRunCycleAccount.account_id == AutomationRun.account_id,
                ),
            )
            .where(AutomationRun.trigger == "manual")
            .where(AutomationRun.status == "running")
            .where(AutomationRun.finished_at.is_(None))
            .where(AutomationRun.scheduled_for <= now_utc)
            .where(
                or_(
                    AutomationRun.started_at <= AutomationRun.scheduled_for,
                    AutomationRun.started_at < stale_started_before,
                )
            )
            .order_by(
                AutomationRun.scheduled_for.asc(),
                func.coalesce(AutomationRunCycleAccount.position, 2147483647).asc(),
                AutomationRun.started_at.asc(),
                AutomationRun.id.asc(),
            )
            .limit(limit)
        )
        if cycle_key is not None:
            stmt = stmt.where(AutomationRun.cycle_key == cycle_key)
        result = await self._session.execute(stmt)
        return [
            self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)
            for run, job_name, model, reasoning_effort in result.all()
        ]

    async def claim_manual_run_execution(
        self,
        run_id: str,
        *,
        observed_started_at: datetime,
        claimed_started_at: datetime,
        stale_started_before: datetime,
    ) -> AutomationRunRecord | None:
        result = await self._session.execute(
            update(AutomationRun)
            .where(AutomationRun.id == run_id)
            .where(AutomationRun.trigger == "manual")
            .where(AutomationRun.status == "running")
            .where(AutomationRun.finished_at.is_(None))
            .where(AutomationRun.account_id.is_not(None))
            .where(AutomationRun.started_at == observed_started_at)
            .where(
                or_(
                    AutomationRun.started_at <= AutomationRun.scheduled_for,
                    AutomationRun.started_at < stale_started_before,
                )
            )
            .values(started_at=claimed_started_at)
            .returning(AutomationRun)
        )
        run = result.scalar_one_or_none()
        await self._session.commit()
        if run is None:
            return None
        return self._run_from_model(run)

    async def claim_scheduled_cycle_run_execution(
        self,
        run_id: str,
        *,
        observed_started_at: datetime,
        claimed_started_at: datetime,
        stale_started_before: datetime,
    ) -> AutomationRunRecord | None:
        result = await self._session.execute(
            update(AutomationRun)
            .where(AutomationRun.id == run_id)
            .where(AutomationRun.trigger == "scheduled")
            .where(AutomationRun.status == "running")
            .where(AutomationRun.finished_at.is_(None))
            .where(AutomationRun.started_at == observed_started_at)
            .where(
                or_(
                    AutomationRun.started_at <= AutomationRun.scheduled_for,
                    AutomationRun.started_at < stale_started_before,
                )
            )
            .values(started_at=claimed_started_at)
            .returning(AutomationRun)
        )
        run = result.scalar_one_or_none()
        await self._session.commit()
        if run is None:
            return None
        return self._run_from_model(run)

    async def skip_unclaimed_manual_run_placeholder(
        self,
        run_id: str,
        *,
        cycle_key: str,
        account_id: str,
        observed_started_at: datetime,
        skipped_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(AutomationRun)
            .where(AutomationRun.id == run_id)
            .where(AutomationRun.trigger == "manual")
            .where(AutomationRun.status == "running")
            .where(AutomationRun.finished_at.is_(None))
            .where(or_(AutomationRun.account_id == account_id, AutomationRun.account_id.is_(None)))
            .where(AutomationRun.started_at == observed_started_at)
            .where(AutomationRun.attempt_count == 0)
            .where(AutomationRun.started_at <= AutomationRun.scheduled_for)
            .values(
                status="partial",
                finished_at=skipped_at,
                account_id=None,
                error_code=None,
                error_message=None,
            )
            .returning(AutomationRun.id)
        )
        skipped_run_id = result.scalar_one_or_none()
        if skipped_run_id is None:
            await self._session.commit()
            return False

        await self._session.execute(
            delete(AutomationRunCycleAccount)
            .where(AutomationRunCycleAccount.cycle_key == cycle_key)
            .where(AutomationRunCycleAccount.account_id == account_id)
        )
        await self._sync_run_cycle_expected_accounts(cycle_key=cycle_key)
        await self._session.commit()
        self._session.expire_all()
        return True

    async def delete_run_cycle_account(self, *, cycle_key: str, account_id: str) -> bool:
        result = await self._session.execute(
            delete(AutomationRunCycleAccount)
            .where(AutomationRunCycleAccount.cycle_key == cycle_key)
            .where(AutomationRunCycleAccount.account_id == account_id)
            .returning(AutomationRunCycleAccount.account_id)
        )
        deleted_account_id = result.scalar_one_or_none()
        if deleted_account_id is not None:
            await self._sync_run_cycle_expected_accounts(cycle_key=cycle_key)
        await self._session.commit()
        self._session.expire_all()
        return deleted_account_id is not None

    async def run_cycle_account_exists(self, *, cycle_key: str, account_id: str) -> bool:
        result = await self._session.execute(
            select(
                exists(
                    select(AutomationRunCycleAccount.account_id)
                    .where(AutomationRunCycleAccount.cycle_key == cycle_key)
                    .where(AutomationRunCycleAccount.account_id == account_id)
                )
            )
        )
        return bool(result.scalar_one())

    async def _sync_run_cycle_expected_accounts(self, *, cycle_key: str) -> None:
        remaining_count = int(
            (
                await self._session.execute(
                    select(func.count(AutomationRunCycleAccount.account_id)).where(
                        AutomationRunCycleAccount.cycle_key == cycle_key
                    )
                )
            ).scalar_one()
            or 0
        )
        await self._session.execute(
            update(AutomationRun)
            .where(AutomationRun.cycle_key == cycle_key)
            .values(cycle_expected_accounts=remaining_count)
        )
        await self._session.execute(
            update(AutomationRunCycle)
            .where(AutomationRunCycle.cycle_key == cycle_key)
            .values(cycle_expected_accounts=remaining_count)
        )

    async def list_runs_page(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        account_ids: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        triggers: Sequence[str] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> tuple[list[AutomationRunRecord], int]:
        conditions = self._build_run_conditions(
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=statuses,
            triggers=triggers,
            job_ids=job_ids,
        )
        stmt = (
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
            .offset(offset)
            .limit(limit)
        )
        if conditions:
            stmt = stmt.where(and_(*conditions))
        result = await self._session.execute(stmt)
        runs = [
            self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)
            for run, job_name, model, reasoning_effort in result.all()
        ]

        count_stmt = (
            select(func.count(AutomationRun.id))
            .select_from(AutomationRun)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
        )
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total = int((await self._session.execute(count_stmt)).scalar_one() or 0)
        return runs, total

    async def list_runs_filtered(
        self,
        *,
        search: str | None = None,
        account_ids: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        triggers: Sequence[str] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> list[AutomationRunRecord]:
        conditions = self._build_run_conditions(
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=statuses,
            triggers=triggers,
            job_ids=job_ids,
        )
        stmt = (
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
        )
        if conditions:
            stmt = stmt.where(and_(*conditions))
        result = await self._session.execute(stmt)
        return [
            self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)
            for run, job_name, model, reasoning_effort in result.all()
        ]

    async def list_run_cycles_page(
        self,
        *,
        limit: int,
        offset: int,
        now_utc: datetime,
        search: str | None = None,
        account_ids: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        triggers: Sequence[str] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> tuple[list[AutomationRunRecord], int]:
        conditions = self._build_run_conditions(
            search=search,
            account_ids=None,
            models=models,
            statuses=None,
            triggers=triggers,
            job_ids=job_ids,
        )
        filtered_runs_stmt = select(
            AutomationRun.id.label("run_id"),
            AutomationRun.cycle_key.label("cycle_key"),
            AutomationRun.trigger.label("trigger"),
            AutomationRun.status.label("status"),
            AutomationRun.account_id.label("account_id"),
            AutomationRun.started_at.label("started_at"),
            AutomationRun.finished_at.label("finished_at"),
            AutomationRun.scheduled_for.label("scheduled_for"),
            AutomationRun.cycle_window_end.label("cycle_window_end"),
            AutomationRun.cycle_expected_accounts.label("cycle_expected_accounts"),
            AutomationRun.attempt_count.label("attempt_count"),
            AutomationJob.include_paused_accounts.label("include_paused_accounts"),
        ).join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
        if conditions:
            filtered_runs_stmt = filtered_runs_stmt.where(and_(*conditions))
        grouped_account_match = self._build_grouped_run_account_match(account_ids=account_ids)
        if grouped_account_match is not None:
            filtered_runs_stmt = filtered_runs_stmt.where(grouped_account_match)
        filtered_runs = filtered_runs_stmt.subquery()
        candidate_cycles = select(filtered_runs.c.cycle_key).distinct().subquery()

        cycle_runs_stmt = (
            select(
                AutomationRun.id.label("run_id"),
                AutomationRun.cycle_key.label("cycle_key"),
                AutomationRun.trigger.label("trigger"),
                AutomationRun.status.label("status"),
                AutomationRun.account_id.label("account_id"),
                AutomationRun.started_at.label("started_at"),
                AutomationRun.finished_at.label("finished_at"),
                AutomationRun.scheduled_for.label("scheduled_for"),
                AutomationRun.cycle_window_end.label("cycle_window_end"),
                AutomationRun.cycle_expected_accounts.label("cycle_expected_accounts"),
                AutomationRun.attempt_count.label("attempt_count"),
                AutomationJob.include_paused_accounts.label("include_paused_accounts"),
                Account.status.label("account_status"),
            )
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .join(candidate_cycles, candidate_cycles.c.cycle_key == AutomationRun.cycle_key)
            .outerjoin(Account, Account.id == AutomationRun.account_id)
        )
        cycle_runs = cycle_runs_stmt.subquery()

        snapshot_accounts_stmt = (
            select(
                AutomationRunCycleAccount.cycle_key.label("cycle_key"),
                func.count(func.distinct(AutomationRunCycleAccount.account_id)).label("included_accounts"),
            )
            .join(candidate_cycles, candidate_cycles.c.cycle_key == AutomationRunCycleAccount.cycle_key)
            .join(AutomationRunCycle, AutomationRunCycle.cycle_key == AutomationRunCycleAccount.cycle_key)
            .outerjoin(Account, Account.id == AutomationRunCycleAccount.account_id)
            .outerjoin(
                AutomationRun,
                and_(
                    AutomationRun.cycle_key == AutomationRunCycleAccount.cycle_key,
                    or_(
                        and_(
                            AutomationRunCycleAccount.slot_key.is_not(None),
                            AutomationRun.slot_key == AutomationRunCycleAccount.slot_key,
                        ),
                        and_(
                            AutomationRunCycleAccount.slot_key.is_(None),
                            AutomationRun.account_id == AutomationRunCycleAccount.account_id,
                        ),
                    ),
                ),
            )
            .where(
                or_(
                    AutomationRun.id.is_not(None),
                    Account.status == AccountStatus.ACTIVE,
                    and_(
                        Account.status == AccountStatus.PAUSED,
                        AutomationRunCycle.include_paused_accounts.is_(True),
                    ),
                )
            )
            .group_by(AutomationRunCycleAccount.cycle_key)
        )
        snapshot_accounts = snapshot_accounts_stmt.subquery()

        ranked_stmt = select(
            filtered_runs.c.run_id,
            filtered_runs.c.cycle_key,
            filtered_runs.c.status.label("fallback_status"),
            func.row_number()
            .over(
                partition_by=filtered_runs.c.cycle_key,
                order_by=(filtered_runs.c.started_at.desc(), filtered_runs.c.run_id.desc()),
            )
            .label("cycle_rank"),
        )
        ranked = ranked_stmt.subquery()

        countable_outcome = or_(
            cycle_runs.c.account_id.is_not(None),
            cycle_runs.c.trigger == "scheduled",
            cycle_runs.c.attempt_count > 0,
        )
        account_is_eligible = and_(
            cycle_runs.c.account_id.is_not(None),
            or_(
                cycle_runs.c.status != "running",
                cycle_runs.c.account_status == AccountStatus.ACTIVE,
                and_(
                    cycle_runs.c.account_status == AccountStatus.PAUSED,
                    cycle_runs.c.include_paused_accounts.is_(True),
                ),
            ),
        )
        hidden_manual_placeholder = and_(
            cycle_runs.c.trigger == "manual",
            cycle_runs.c.status == "running",
            cycle_runs.c.finished_at.is_(None),
            cycle_runs.c.attempt_count == 0,
            cycle_runs.c.started_at <= cycle_runs.c.scheduled_for,
            ~account_is_eligible,
        )
        visible_countable_outcome = and_(countable_outcome, ~hidden_manual_placeholder)
        completed_countable_run = case(
            (and_(cycle_runs.c.status != "running", visible_countable_outcome), 1),
            else_=0,
        )

        cycle_agg_stmt = select(
            cycle_runs.c.cycle_key.label("cycle_key"),
            func.max(case((cycle_runs.c.trigger == "manual", 1), else_=0)).label("has_manual_trigger"),
            func.min(case((cycle_runs.c.trigger == "manual", cycle_runs.c.scheduled_for), else_=None)).label(
                "manual_cycle_started_at"
            ),
            func.min(case((cycle_runs.c.trigger != "manual", cycle_runs.c.started_at), else_=None)).label(
                "non_manual_cycle_started_at"
            ),
            func.sum(completed_countable_run).label("completed_accounts"),
            func.sum(case((and_(visible_countable_outcome, cycle_runs.c.status == "success"), 1), else_=0)).label(
                "success_count"
            ),
            func.sum(case((and_(visible_countable_outcome, cycle_runs.c.status == "failed"), 1), else_=0)).label(
                "failed_count"
            ),
            func.sum(case((and_(visible_countable_outcome, cycle_runs.c.status == "partial"), 1), else_=0)).label(
                "partial_count"
            ),
            func.sum(case((and_(~hidden_manual_placeholder, cycle_runs.c.status == "running"), 1), else_=0)).label(
                "running_count"
            ),
            func.sum(case((visible_countable_outcome, 1), else_=0)).label("visible_accounts"),
            func.max(func.coalesce(cycle_runs.c.cycle_expected_accounts, 0)).label("snapshot_expected_accounts"),
            func.max(func.coalesce(cycle_runs.c.cycle_window_end, cycle_runs.c.scheduled_for)).label("window_end"),
        ).group_by(cycle_runs.c.cycle_key)
        cycle_agg = cycle_agg_stmt.subquery()

        cycle_rows_stmt = (
            select(
                ranked.c.run_id,
                ranked.c.cycle_key,
                case(
                    (cycle_agg.c.has_manual_trigger == 1, cycle_agg.c.manual_cycle_started_at),
                    else_=cycle_agg.c.non_manual_cycle_started_at,
                ).label("cycle_started_at"),
                cycle_agg.c.has_manual_trigger,
                ranked.c.fallback_status,
                cycle_agg.c.completed_accounts,
                cycle_agg.c.success_count,
                cycle_agg.c.failed_count,
                cycle_agg.c.partial_count,
                cycle_agg.c.running_count,
                cycle_agg.c.visible_accounts.label("expected_accounts"),
                cycle_agg.c.window_end,
                snapshot_accounts.c.included_accounts,
            )
            .join(cycle_agg, cycle_agg.c.cycle_key == ranked.c.cycle_key)
            .outerjoin(snapshot_accounts, snapshot_accounts.c.cycle_key == ranked.c.cycle_key)
            .where(ranked.c.cycle_rank == 1)
        )
        cycle_rows = cycle_rows_stmt.subquery()

        expected_accounts_expr = case(
            (cycle_rows.c.has_manual_trigger == 1, cycle_rows.c.expected_accounts),
            (
                func.coalesce(cycle_rows.c.included_accounts, cycle_rows.c.expected_accounts)
                > cycle_rows.c.expected_accounts,
                func.coalesce(cycle_rows.c.included_accounts, cycle_rows.c.expected_accounts),
            ),
            else_=cycle_rows.c.expected_accounts,
        )

        effective_total_expr = case(
            (expected_accounts_expr > cycle_rows.c.completed_accounts, expected_accounts_expr),
            else_=cycle_rows.c.completed_accounts,
        )
        pending_expr = effective_total_expr - cycle_rows.c.completed_accounts
        effective_status_expr = case(
            (cycle_rows.c.running_count > 0, "running"),
            (and_(pending_expr > 0, now_utc <= cycle_rows.c.window_end), "running"),
            (pending_expr > 0, case((cycle_rows.c.completed_accounts > 0, "partial"), else_="failed")),
            (
                and_(
                    cycle_rows.c.success_count > 0,
                    cycle_rows.c.failed_count == 0,
                    cycle_rows.c.partial_count == 0,
                ),
                "success",
            ),
            (
                and_(
                    cycle_rows.c.success_count > 0,
                    or_(cycle_rows.c.failed_count > 0, cycle_rows.c.partial_count > 0),
                ),
                "partial",
            ),
            (
                and_(
                    cycle_rows.c.failed_count > 0,
                    cycle_rows.c.success_count == 0,
                    cycle_rows.c.partial_count == 0,
                ),
                "failed",
            ),
            (cycle_rows.c.partial_count > 0, "partial"),
            else_=cycle_rows.c.fallback_status,
        ).label("effective_status")

        cycles_with_status_stmt = select(
            cycle_rows.c.run_id,
            cycle_rows.c.cycle_key,
            cycle_rows.c.cycle_started_at,
            effective_status_expr,
        )
        if statuses:
            cycles_with_status_stmt = cycles_with_status_stmt.where(effective_status_expr.in_(list(statuses)))
        cycles_with_status = cycles_with_status_stmt.subquery()

        page_ids_stmt = (
            select(cycles_with_status.c.run_id)
            .order_by(cycles_with_status.c.cycle_started_at.desc(), cycles_with_status.c.run_id.desc())
            .offset(offset)
            .limit(limit)
        )
        run_ids_rows = await self._session.execute(page_ids_stmt)
        run_ids = [value for (value,) in run_ids_rows.all() if value]
        if not run_ids:
            count_stmt = select(func.count()).select_from(cycles_with_status)
            total = int((await self._session.execute(count_stmt)).scalar_one() or 0)
            return [], total

        runs_stmt = (
            select(AutomationRun, AutomationJob.name, AutomationJob.model, AutomationJob.reasoning_effort)
            .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            .where(AutomationRun.id.in_(run_ids))
        )
        runs_rows = await self._session.execute(runs_stmt)
        runs_by_id = {
            run.id: self._run_from_model(run, job_name=job_name, model=model, reasoning_effort=reasoning_effort)
            for run, job_name, model, reasoning_effort in runs_rows.all()
        }
        ordered_runs = [runs_by_id[run_id] for run_id in run_ids if run_id in runs_by_id]

        count_stmt = select(func.count()).select_from(cycles_with_status)
        total = int((await self._session.execute(count_stmt)).scalar_one() or 0)
        return ordered_runs, total

    async def list_run_filter_options(
        self,
        *,
        now_utc: datetime | None = None,
        search: str | None = None,
        account_ids: Sequence[str] | None = None,
        models: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        triggers: Sequence[str] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> AutomationRunsFilterOptionsRecord:
        if statuses:
            conditions = self._build_run_conditions(
                search=search,
                account_ids=None,
                models=models,
                statuses=None,
                triggers=triggers,
                job_ids=job_ids,
            )
            filtered_runs_stmt = select(
                AutomationRun.id.label("run_id"),
                AutomationRun.cycle_key.label("cycle_key"),
                AutomationRun.status.label("status"),
                AutomationRun.account_id.label("account_id"),
                AutomationRun.started_at.label("started_at"),
                AutomationRun.finished_at.label("finished_at"),
                AutomationRun.scheduled_for.label("scheduled_for"),
                AutomationRun.cycle_window_end.label("cycle_window_end"),
                AutomationRun.cycle_expected_accounts.label("cycle_expected_accounts"),
                AutomationRun.trigger.label("trigger"),
                AutomationRun.attempt_count.label("attempt_count"),
                AutomationJob.include_paused_accounts.label("include_paused_accounts"),
            ).join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
            if conditions:
                filtered_runs_stmt = filtered_runs_stmt.where(and_(*conditions))
            grouped_account_match = self._build_grouped_run_account_match(account_ids=account_ids)
            if grouped_account_match is not None:
                filtered_runs_stmt = filtered_runs_stmt.where(grouped_account_match)
            filtered_runs = filtered_runs_stmt.subquery()
            candidate_cycles = select(filtered_runs.c.cycle_key).distinct().subquery()

            cycle_runs_stmt = (
                select(
                    AutomationRun.id.label("run_id"),
                    AutomationRun.cycle_key.label("cycle_key"),
                    AutomationRun.status.label("status"),
                    AutomationRun.account_id.label("account_id"),
                    AutomationRun.started_at.label("started_at"),
                    AutomationRun.finished_at.label("finished_at"),
                    AutomationRun.scheduled_for.label("scheduled_for"),
                    AutomationRun.cycle_window_end.label("cycle_window_end"),
                    AutomationRun.cycle_expected_accounts.label("cycle_expected_accounts"),
                    AutomationRun.trigger.label("trigger"),
                    AutomationRun.attempt_count.label("attempt_count"),
                    AutomationJob.include_paused_accounts.label("include_paused_accounts"),
                    Account.status.label("account_status"),
                )
                .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
                .join(candidate_cycles, candidate_cycles.c.cycle_key == AutomationRun.cycle_key)
                .outerjoin(Account, Account.id == AutomationRun.account_id)
            )
            cycle_runs = cycle_runs_stmt.subquery()

            snapshot_accounts_stmt = (
                select(
                    AutomationRunCycleAccount.cycle_key.label("cycle_key"),
                    func.count(func.distinct(AutomationRunCycleAccount.account_id)).label("included_accounts"),
                )
                .join(candidate_cycles, candidate_cycles.c.cycle_key == AutomationRunCycleAccount.cycle_key)
                .join(AutomationRunCycle, AutomationRunCycle.cycle_key == AutomationRunCycleAccount.cycle_key)
                .outerjoin(Account, Account.id == AutomationRunCycleAccount.account_id)
                .outerjoin(
                    AutomationRun,
                    and_(
                        AutomationRun.cycle_key == AutomationRunCycleAccount.cycle_key,
                        or_(
                            and_(
                                AutomationRunCycleAccount.slot_key.is_not(None),
                                AutomationRun.slot_key == AutomationRunCycleAccount.slot_key,
                            ),
                            and_(
                                AutomationRunCycleAccount.slot_key.is_(None),
                                AutomationRun.account_id == AutomationRunCycleAccount.account_id,
                            ),
                        ),
                    ),
                )
                .where(
                    or_(
                        AutomationRun.id.is_not(None),
                        Account.status == AccountStatus.ACTIVE,
                        and_(
                            Account.status == AccountStatus.PAUSED,
                            AutomationRunCycle.include_paused_accounts.is_(True),
                        ),
                    )
                )
                .group_by(AutomationRunCycleAccount.cycle_key)
            )
            snapshot_accounts = snapshot_accounts_stmt.subquery()

            ranked_stmt = select(
                filtered_runs.c.run_id,
                filtered_runs.c.cycle_key,
                filtered_runs.c.status.label("fallback_status"),
                func.row_number()
                .over(
                    partition_by=filtered_runs.c.cycle_key,
                    order_by=(filtered_runs.c.started_at.desc(), filtered_runs.c.run_id.desc()),
                )
                .label("cycle_rank"),
            )
            ranked = ranked_stmt.subquery()

            countable_outcome = or_(
                cycle_runs.c.account_id.is_not(None),
                cycle_runs.c.trigger == "scheduled",
                cycle_runs.c.attempt_count > 0,
            )
            account_is_eligible = and_(
                cycle_runs.c.account_id.is_not(None),
                or_(
                    cycle_runs.c.status != "running",
                    cycle_runs.c.account_status == AccountStatus.ACTIVE,
                    and_(
                        cycle_runs.c.account_status == AccountStatus.PAUSED,
                        cycle_runs.c.include_paused_accounts.is_(True),
                    ),
                ),
            )
            hidden_manual_placeholder = and_(
                cycle_runs.c.trigger == "manual",
                cycle_runs.c.status == "running",
                cycle_runs.c.finished_at.is_(None),
                cycle_runs.c.attempt_count == 0,
                cycle_runs.c.started_at <= cycle_runs.c.scheduled_for,
                ~account_is_eligible,
            )
            visible_countable_outcome = and_(countable_outcome, ~hidden_manual_placeholder)
            completed_countable_run = case(
                (and_(cycle_runs.c.status != "running", visible_countable_outcome), 1),
                else_=0,
            )

            cycle_agg_stmt = select(
                cycle_runs.c.cycle_key.label("cycle_key"),
                func.max(case((cycle_runs.c.trigger == "manual", 1), else_=0)).label("has_manual_trigger"),
                func.sum(completed_countable_run).label("completed_accounts"),
                func.sum(case((and_(visible_countable_outcome, cycle_runs.c.status == "success"), 1), else_=0)).label(
                    "success_count"
                ),
                func.sum(case((and_(visible_countable_outcome, cycle_runs.c.status == "failed"), 1), else_=0)).label(
                    "failed_count"
                ),
                func.sum(case((and_(visible_countable_outcome, cycle_runs.c.status == "partial"), 1), else_=0)).label(
                    "partial_count"
                ),
                func.sum(case((and_(~hidden_manual_placeholder, cycle_runs.c.status == "running"), 1), else_=0)).label(
                    "running_count"
                ),
                func.sum(case((visible_countable_outcome, 1), else_=0)).label("visible_accounts"),
                func.max(func.coalesce(cycle_runs.c.cycle_expected_accounts, 0)).label("snapshot_expected_accounts"),
                func.max(func.coalesce(cycle_runs.c.cycle_window_end, cycle_runs.c.scheduled_for)).label("window_end"),
            ).group_by(cycle_runs.c.cycle_key)
            cycle_agg = cycle_agg_stmt.subquery()

            cycle_rows_stmt = (
                select(
                    ranked.c.cycle_key,
                    cycle_agg.c.has_manual_trigger,
                    ranked.c.fallback_status,
                    cycle_agg.c.completed_accounts,
                    cycle_agg.c.success_count,
                    cycle_agg.c.failed_count,
                    cycle_agg.c.partial_count,
                    cycle_agg.c.running_count,
                    cycle_agg.c.visible_accounts.label("expected_accounts"),
                    cycle_agg.c.window_end,
                    snapshot_accounts.c.included_accounts,
                )
                .join(cycle_agg, cycle_agg.c.cycle_key == ranked.c.cycle_key)
                .outerjoin(snapshot_accounts, snapshot_accounts.c.cycle_key == ranked.c.cycle_key)
                .where(ranked.c.cycle_rank == 1)
            )
            cycle_rows = cycle_rows_stmt.subquery()

            expected_accounts_expr = case(
                (cycle_rows.c.has_manual_trigger == 1, cycle_rows.c.expected_accounts),
                (
                    func.coalesce(cycle_rows.c.included_accounts, cycle_rows.c.expected_accounts)
                    > cycle_rows.c.expected_accounts,
                    func.coalesce(cycle_rows.c.included_accounts, cycle_rows.c.expected_accounts),
                ),
                else_=cycle_rows.c.expected_accounts,
            )
            effective_total_expr = case(
                (expected_accounts_expr > cycle_rows.c.completed_accounts, expected_accounts_expr),
                else_=cycle_rows.c.completed_accounts,
            )
            pending_expr = effective_total_expr - cycle_rows.c.completed_accounts
            now = now_utc or utcnow()
            effective_status_expr = case(
                (cycle_rows.c.running_count > 0, "running"),
                (and_(pending_expr > 0, now <= cycle_rows.c.window_end), "running"),
                (pending_expr > 0, case((cycle_rows.c.completed_accounts > 0, "partial"), else_="failed")),
                (
                    and_(
                        cycle_rows.c.success_count > 0,
                        cycle_rows.c.failed_count == 0,
                        cycle_rows.c.partial_count == 0,
                    ),
                    "success",
                ),
                (
                    and_(
                        cycle_rows.c.success_count > 0,
                        or_(cycle_rows.c.failed_count > 0, cycle_rows.c.partial_count > 0),
                    ),
                    "partial",
                ),
                (
                    and_(
                        cycle_rows.c.failed_count > 0,
                        cycle_rows.c.success_count == 0,
                        cycle_rows.c.partial_count == 0,
                    ),
                    "failed",
                ),
                (cycle_rows.c.partial_count > 0, "partial"),
                else_=cycle_rows.c.fallback_status,
            )
            matching_cycles = select(cycle_rows.c.cycle_key).where(effective_status_expr.in_(list(statuses))).subquery()

            account_stmt = (
                select(AutomationRun.account_id)
                .distinct()
                .join(matching_cycles, matching_cycles.c.cycle_key == AutomationRun.cycle_key)
                .where(AutomationRun.account_id.is_not(None))
                .order_by(AutomationRun.account_id.asc())
            )
            model_stmt = (
                select(func.coalesce(AutomationRun.model, AutomationJob.model))
                .select_from(AutomationRun)
                .distinct()
                .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
                .join(matching_cycles, matching_cycles.c.cycle_key == AutomationRun.cycle_key)
                .order_by(func.coalesce(AutomationRun.model, AutomationJob.model).asc())
            )
            status_stmt = (
                select(AutomationRun.status)
                .distinct()
                .join(matching_cycles, matching_cycles.c.cycle_key == AutomationRun.cycle_key)
                .order_by(AutomationRun.status.asc())
            )
            trigger_stmt = (
                select(AutomationRun.trigger)
                .distinct()
                .join(matching_cycles, matching_cycles.c.cycle_key == AutomationRun.cycle_key)
                .order_by(AutomationRun.trigger.asc())
            )
        else:
            conditions = self._build_run_conditions(
                search=search,
                account_ids=account_ids,
                models=models,
                statuses=statuses,
                triggers=triggers,
                job_ids=job_ids,
            )
            account_stmt = (
                select(AutomationRun.account_id)
                .distinct()
                .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
                .where(AutomationRun.account_id.is_not(None))
                .order_by(AutomationRun.account_id.asc())
            )
            model_stmt = (
                select(func.coalesce(AutomationRun.model, AutomationJob.model))
                .select_from(AutomationRun)
                .distinct()
                .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
                .order_by(func.coalesce(AutomationRun.model, AutomationJob.model).asc())
            )
            status_stmt = (
                select(AutomationRun.status)
                .distinct()
                .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
                .order_by(AutomationRun.status.asc())
            )
            trigger_stmt = (
                select(AutomationRun.trigger)
                .distinct()
                .join(AutomationJob, AutomationJob.id == AutomationRun.job_id)
                .order_by(AutomationRun.trigger.asc())
            )
            if conditions:
                clause = and_(*conditions)
                account_stmt = account_stmt.where(clause)
                model_stmt = model_stmt.where(clause)
                status_stmt = status_stmt.where(clause)
                trigger_stmt = trigger_stmt.where(clause)

        account_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)
        status_rows = await self._session.execute(status_stmt)
        trigger_rows = await self._session.execute(trigger_stmt)
        return AutomationRunsFilterOptionsRecord(
            account_ids=[value for (value,) in account_rows.all() if value],
            models=[value for (value,) in model_rows.all() if value],
            statuses=[value for (value,) in status_rows.all() if value],
            triggers=[value for (value,) in trigger_rows.all() if value],
        )

    async def get_latest_runs_by_job_ids(self, job_ids: Sequence[str]) -> dict[str, AutomationRunRecord]:
        if not job_ids:
            return {}
        normalized_job_ids = list(dict.fromkeys(job_id for job_id in job_ids if job_id))
        if not normalized_job_ids:
            return {}

        cycle_agg = (
            select(
                AutomationRun.job_id.label("job_id"),
                AutomationRun.cycle_key.label("cycle_key"),
                func.max(case((AutomationRun.trigger == "manual", 1), else_=0)).label("has_manual_trigger"),
                func.min(case((AutomationRun.trigger == "manual", AutomationRun.scheduled_for), else_=None)).label(
                    "manual_cycle_started_at"
                ),
                func.min(case((AutomationRun.trigger != "manual", AutomationRun.started_at), else_=None)).label(
                    "non_manual_cycle_started_at"
                ),
            )
            .where(AutomationRun.job_id.in_(normalized_job_ids))
            .group_by(AutomationRun.job_id, AutomationRun.cycle_key)
            .subquery()
        )
        cycle_started_at = case(
            (cycle_agg.c.has_manual_trigger == 1, cycle_agg.c.manual_cycle_started_at),
            else_=cycle_agg.c.non_manual_cycle_started_at,
        )
        ranked_cycles = select(
            cycle_agg.c.job_id,
            cycle_agg.c.cycle_key,
            func.row_number()
            .over(
                partition_by=cycle_agg.c.job_id,
                order_by=(cycle_started_at.desc(), cycle_agg.c.cycle_key.desc()),
            )
            .label("cycle_rank"),
        ).subquery()
        ranked_runs = (
            select(
                AutomationRun.id.label("run_id"),
                AutomationRun.job_id.label("job_id"),
                AutomationRun.cycle_key.label("cycle_key"),
                func.row_number()
                .over(
                    partition_by=(AutomationRun.job_id, AutomationRun.cycle_key),
                    order_by=(AutomationRun.started_at.desc(), AutomationRun.id.desc()),
                )
                .label("run_rank"),
            )
            .where(AutomationRun.job_id.in_(normalized_job_ids))
            .subquery()
        )
        result = await self._session.execute(
            select(AutomationRun)
            .join(ranked_runs, ranked_runs.c.run_id == AutomationRun.id)
            .join(
                ranked_cycles,
                and_(
                    ranked_cycles.c.job_id == ranked_runs.c.job_id,
                    ranked_cycles.c.cycle_key == ranked_runs.c.cycle_key,
                ),
            )
            .where(ranked_runs.c.run_rank == 1)
            .where(ranked_cycles.c.cycle_rank == 1)
            .order_by(AutomationRun.job_id.asc())
        )
        latest: dict[str, AutomationRunRecord] = {}
        for run in result.scalars().all():
            if run.job_id in latest:
                continue
            latest[run.job_id] = self._run_from_model(run)
        return latest

    @staticmethod
    def _job_from_model(job: AutomationJob) -> AutomationJobRecord:
        sorted_accounts = sorted(job.account_links, key=lambda link: link.position)
        return AutomationJobRecord(
            id=job.id,
            name=job.name,
            enabled=job.enabled,
            schedule_type=job.schedule_type,
            schedule_time=job.schedule_time,
            schedule_timezone=job.schedule_timezone,
            schedule_days=_parse_schedule_days(job.schedule_days),
            schedule_threshold_minutes=job.schedule_threshold_minutes,
            include_paused_accounts=job.include_paused_accounts,
            account_scope_all=job.account_scope_all,
            model=job.model,
            reasoning_effort=job.reasoning_effort,
            prompt=job.prompt,
            account_ids=[link.account_id for link in sorted_accounts],
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @staticmethod
    def _run_from_model(
        run: AutomationRun,
        *,
        job_name: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> AutomationRunRecord:
        return AutomationRunRecord(
            id=run.id,
            job_id=run.job_id,
            job_name=job_name,
            model=run.model or model,
            reasoning_effort=run.reasoning_effort if run.model is not None else reasoning_effort,
            prompt=run.prompt,
            trigger=run.trigger,
            status=run.status,
            slot_key=run.slot_key,
            cycle_key=run.cycle_key,
            cycle_expected_accounts=run.cycle_expected_accounts,
            cycle_window_end=run.cycle_window_end,
            scheduled_for=run.scheduled_for,
            started_at=run.started_at,
            finished_at=run.finished_at,
            account_id=run.account_id,
            error_code=run.error_code,
            error_message=run.error_message,
            attempt_count=run.attempt_count,
        )

    @staticmethod
    def _run_cycle_from_model(cycle: AutomationRunCycle) -> AutomationRunCycleRecord:
        cycle_accounts = sorted(cycle.cycle_accounts, key=lambda entry: (entry.position, entry.account_id))
        return AutomationRunCycleRecord(
            cycle_key=cycle.cycle_key,
            job_id=cycle.job_id,
            trigger=cycle.trigger,
            cycle_expected_accounts=cycle.cycle_expected_accounts,
            cycle_window_end=cycle.cycle_window_end,
            include_paused_accounts=cycle.include_paused_accounts,
            accounts=[
                AutomationRunCycleAccountRecord(
                    account_id=entry.account_id,
                    slot_key=entry.slot_key,
                    position=entry.position,
                    scheduled_for=entry.scheduled_for,
                )
                for entry in cycle_accounts
            ],
            created_at=cycle.created_at,
        )

    @staticmethod
    def _build_job_conditions(
        *,
        search: str | None,
        account_ids: Sequence[str] | None,
        models: Sequence[str] | None,
        statuses: Sequence[str] | None,
        schedule_types: Sequence[str] | None,
    ) -> list:
        conditions = []
        normalized_search = (search or "").strip()
        if normalized_search:
            like = f"%{normalized_search}%"
            conditions.append(
                or_(
                    AutomationJob.id.ilike(like),
                    AutomationJob.name.ilike(like),
                    AutomationJob.prompt.ilike(like),
                    AutomationJob.model.ilike(like),
                    AutomationJob.reasoning_effort.ilike(like),
                )
            )
        normalized_accounts = [value.strip() for value in (account_ids or []) if value and value.strip()]
        if normalized_accounts:
            matching_account_links = select(AutomationJobAccount.job_id).where(
                AutomationJobAccount.account_id.in_(normalized_accounts)
            )
            job_has_all_account_scope = AutomationJob.account_scope_all.is_(True)
            conditions.append(
                or_(
                    AutomationJob.id.in_(matching_account_links),
                    job_has_all_account_scope,
                )
            )
        normalized_models = [value.strip() for value in (models or []) if value and value.strip()]
        if normalized_models:
            conditions.append(AutomationJob.model.in_(normalized_models))
        normalized_types = [value.strip() for value in (schedule_types or []) if value and value.strip()]
        if normalized_types:
            conditions.append(AutomationJob.schedule_type.in_(normalized_types))
        normalized_statuses = {value.strip().lower() for value in (statuses or []) if value and value.strip()}
        if normalized_statuses and "all" not in normalized_statuses:
            enabled_values: list[bool] = []
            if "enabled" in normalized_statuses:
                enabled_values.append(True)
            if "disabled" in normalized_statuses:
                enabled_values.append(False)
            if enabled_values:
                conditions.append(AutomationJob.enabled.in_(enabled_values))
            else:
                conditions.append(AutomationJob.id == "__none__")
        return conditions

    @staticmethod
    def _build_run_conditions(
        *,
        search: str | None,
        account_ids: Sequence[str] | None,
        models: Sequence[str] | None,
        statuses: Sequence[str] | None,
        triggers: Sequence[str] | None,
        job_ids: Sequence[str] | None,
    ) -> list:
        conditions = []
        normalized_search = (search or "").strip()
        run_model = func.coalesce(AutomationRun.model, AutomationJob.model)
        run_reasoning_effort = func.coalesce(AutomationRun.reasoning_effort, AutomationJob.reasoning_effort)
        if normalized_search:
            like = f"%{normalized_search}%"
            conditions.append(
                or_(
                    AutomationRun.id.ilike(like),
                    AutomationRun.job_id.ilike(like),
                    AutomationRun.account_id.ilike(like),
                    AutomationRun.error_code.ilike(like),
                    AutomationRun.error_message.ilike(like),
                    AutomationJob.name.ilike(like),
                    run_model.ilike(like),
                    run_reasoning_effort.ilike(like),
                )
            )
        normalized_accounts = [value.strip() for value in (account_ids or []) if value and value.strip()]
        if normalized_accounts:
            conditions.append(AutomationRun.account_id.in_(normalized_accounts))
        normalized_models = [value.strip() for value in (models or []) if value and value.strip()]
        if normalized_models:
            conditions.append(run_model.in_(normalized_models))
        normalized_statuses = [value.strip().lower() for value in (statuses or []) if value and value.strip()]
        if normalized_statuses:
            conditions.append(AutomationRun.status.in_(normalized_statuses))
        normalized_triggers = [value.strip().lower() for value in (triggers or []) if value and value.strip()]
        if normalized_triggers:
            conditions.append(AutomationRun.trigger.in_(normalized_triggers))
        normalized_job_ids = [value.strip() for value in (job_ids or []) if value and value.strip()]
        if normalized_job_ids:
            conditions.append(AutomationRun.job_id.in_(normalized_job_ids))
        return conditions

    @staticmethod
    def _build_grouped_run_account_match(
        *,
        account_ids: Sequence[str] | None,
    ):
        normalized_accounts = [value.strip() for value in (account_ids or []) if value and value.strip()]
        if not normalized_accounts:
            return None
        snapshot_cycle_keys = select(AutomationRunCycleAccount.cycle_key).where(
            AutomationRunCycleAccount.account_id.in_(normalized_accounts)
        )
        return or_(
            AutomationRun.account_id.in_(normalized_accounts),
            AutomationRun.cycle_key.in_(snapshot_cycle_keys),
        )


def _parse_schedule_days(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_AUTOMATION_SCHEDULE_DAYS)
    parsed = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not parsed:
        return list(DEFAULT_AUTOMATION_SCHEDULE_DAYS)
    return parsed


def _serialize_schedule_days(days: Sequence[str]) -> str:
    if not days:
        return ",".join(DEFAULT_AUTOMATION_SCHEDULE_DAYS)
    normalized = [day.strip().lower() for day in days if day.strip()]
    if not normalized:
        return ",".join(DEFAULT_AUTOMATION_SCHEDULE_DAYS)
    return ",".join(normalized)


def _automation_run_execution_claim_stale_started_before(now_utc: datetime) -> datetime:
    settings = get_settings()
    timeout_seconds = max(30.0, settings.compact_request_budget_seconds + 30.0)
    return now_utc - timedelta(seconds=timeout_seconds)


def _scheduled_slot_key(job_id: str, *, account_id: str, due_slot: datetime) -> str:
    seed = f"{job_id}:{due_slot.isoformat()}:{account_id}"
    digest = sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"scheduled:{job_id}:{digest}"


def _cycle_account_slot_key(*, job_id: str, trigger: str, account_id: str, due_slot: datetime | None) -> str | None:
    if trigger != "scheduled":
        return None
    if due_slot is None:
        return None
    return _scheduled_slot_key(job_id, account_id=account_id, due_slot=due_slot)


def _parse_scheduled_cycle_due_slot(cycle_key: str, *, job_id: str) -> datetime | None:
    parts = cycle_key.split(":", maxsplit=2)
    if len(parts) != 3 or parts[0] != "scheduled" or parts[1] != job_id:
        return None
    try:
        return datetime.fromisoformat(parts[2].removesuffix("Z"))
    except ValueError:
        return None
