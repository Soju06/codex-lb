from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.auth.refresh import RefreshError
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy import compact_responses as core_compact_responses
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import get_model_registry
from app.core.openai.requests import ResponsesCompactRequest, ResponsesReasoning
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.automations.repository import (
    AutomationJobRecord,
    AutomationRunCycleRecord,
    AutomationRunRecord,
    AutomationsRepository,
)
from app.modules.request_logs.repository import RequestLogsRepository

AUTOMATION_SCHEDULE_DAILY = "daily"
AUTOMATION_RUN_TRIGGER_SCHEDULED = "scheduled"
AUTOMATION_RUN_TRIGGER_MANUAL = "manual"
AUTOMATION_RUN_STATUS_RUNNING = "running"
AUTOMATION_RUN_STATUS_SUCCESS = "success"
AUTOMATION_RUN_STATUS_FAILED = "failed"
AUTOMATION_RUN_STATUS_PARTIAL = "partial"
DEFAULT_AUTOMATION_PROMPT = "ping"
AUTOMATION_SERVER_DEFAULT_TIMEZONE = "server_default"
AUTOMATION_SERVER_DEFAULT_TIMEZONE_ALIASES = frozenset({"server_default", "server default", "default"})
AUTOMATION_WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
AUTOMATION_DEFAULT_WEEKDAYS = list(AUTOMATION_WEEKDAY_CODES)
AUTOMATION_DEFAULT_THRESHOLD_MINUTES = 0
AUTOMATION_MAX_THRESHOLD_MINUTES = 240
_WEEKDAY_INDEX_TO_CODE = {index: code for index, code in enumerate(AUTOMATION_WEEKDAY_CODES)}

_RETRYABLE_ACCOUNT_FAILURE_CODES = frozenset(
    {
        "rate_limit_exceeded",
        "usage_limit_reached",
        "insufficient_quota",
        "usage_not_included",
        "quota_exceeded",
        "account_deactivated",
        "invalid_api_key",
        "authentication_error",
        "upstream_unavailable",
        "server_error",
        "upstream_error",
        *PERMANENT_FAILURE_CODES.keys(),
    }
)
_AUTOMATION_ALWAYS_SKIPPED_ACCOUNT_STATUSES = frozenset(
    {
        AccountStatus.DEACTIVATED,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    }
)

logger = logging.getLogger(__name__)


class AutomationValidationError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class AutomationNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class AutomationScheduleData:
    type: str
    time: str
    timezone: str
    days: list[str]
    threshold_minutes: int


@dataclass(frozen=True, slots=True)
class AutomationRunData:
    id: str
    job_id: str
    job_name: str | None
    model: str | None
    reasoning_effort: str | None
    trigger: str
    status: str
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime | None
    account_id: str | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    effective_status: str | None = None
    total_accounts: int | None = None
    completed_accounts: int | None = None
    pending_accounts: int | None = None
    cycle_key: str | None = None


@dataclass(frozen=True, slots=True)
class AutomationJobData:
    id: str
    name: str
    enabled: bool
    include_paused_accounts: bool
    schedule: AutomationScheduleData
    model: str
    reasoning_effort: str | None
    prompt: str
    account_ids: list[str]
    next_run_at: datetime | None
    last_run: AutomationRunData | None


@dataclass(frozen=True, slots=True)
class AutomationJobsPageData:
    items: list[AutomationJobData]
    total: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class AutomationRunsPageData:
    items: list[AutomationRunData]
    total: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class AutomationJobFilterOptionsData:
    account_ids: list[str]
    models: list[str]
    statuses: list[str]
    schedule_types: list[str]


@dataclass(frozen=True, slots=True)
class AutomationRunFilterOptionsData:
    account_ids: list[str]
    models: list[str]
    statuses: list[str]
    triggers: list[str]


@dataclass(frozen=True, slots=True)
class AutomationRunAccountStateData:
    account_id: str
    status: str
    run_id: str | None
    scheduled_for: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class AutomationRunDetailsData:
    run: AutomationRunData
    accounts: list[AutomationRunAccountStateData]
    total_accounts: int
    completed_accounts: int
    pending_accounts: int


@dataclass(frozen=True, slots=True)
class _AutomationRunCycleSummary:
    cycle_key: str
    cycle_started_at: datetime
    cycle_finished_at: datetime | None
    effective_status: str
    total_accounts: int
    completed_accounts: int
    pending_accounts: int
    error_code: str | None
    error_message: str | None
    accounts: list[AutomationRunAccountStateData]


@dataclass(frozen=True, slots=True)
class AutomationJobCreateInput:
    name: str
    enabled: bool
    include_paused_accounts: bool
    schedule_type: str
    schedule_time: str
    schedule_timezone: str
    schedule_days: list[str] | None
    schedule_threshold_minutes: int | None
    model: str
    reasoning_effort: str | None
    prompt: str | None
    account_ids: list[str]


@dataclass(frozen=True, slots=True)
class AutomationJobUpdateInput:
    name: str | None = None
    enabled: bool | None = None
    include_paused_accounts: bool | None = None
    schedule_type: str | None = None
    schedule_time: str | None = None
    schedule_timezone: str | None = None
    schedule_days: list[str] | None = None
    schedule_threshold_minutes: int | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    reasoning_effort_set: bool = False
    prompt: str | None = None
    account_ids: list[str] | None = None


def parse_schedule_time_hhmm(value: str) -> tuple[int, int]:
    parts = value.split(":", maxsplit=1)
    if len(parts) != 2:
        raise AutomationValidationError("Schedule time must use HH:MM format", code="invalid_schedule_time")
    hour_raw, minute_raw = parts
    if len(hour_raw) != 2 or len(minute_raw) != 2:
        raise AutomationValidationError("Schedule time must use HH:MM format", code="invalid_schedule_time")
    if not (hour_raw.isdigit() and minute_raw.isdigit()):
        raise AutomationValidationError("Schedule time must use HH:MM format", code="invalid_schedule_time")
    hour = int(hour_raw)
    minute = int(minute_raw)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise AutomationValidationError("Schedule time is out of range", code="invalid_schedule_time")
    return hour, minute


def normalize_schedule_time(value: str) -> str:
    hour, minute = parse_schedule_time_hhmm(value.strip())
    return f"{hour:02d}:{minute:02d}"


def validate_timezone(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AutomationValidationError("Schedule timezone is required", code="invalid_schedule_timezone")
    if normalized.lower() in AUTOMATION_SERVER_DEFAULT_TIMEZONE_ALIASES:
        return AUTOMATION_SERVER_DEFAULT_TIMEZONE
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise AutomationValidationError(
            f"Unknown timezone: {normalized}",
            code="invalid_schedule_timezone",
        ) from exc
    return normalized


def normalize_schedule_days(value: list[str] | None) -> list[str]:
    if value is None:
        return list(AUTOMATION_DEFAULT_WEEKDAYS)

    normalized: list[str] = []
    for day in value:
        day_code = day.strip().lower()
        if not day_code:
            continue
        if day_code not in AUTOMATION_WEEKDAY_CODES:
            raise AutomationValidationError(
                f"Unsupported schedule day: {day}",
                code="invalid_schedule_days",
            )
        if day_code not in normalized:
            normalized.append(day_code)

    if not normalized:
        raise AutomationValidationError(
            "At least one schedule day is required",
            code="invalid_schedule_days",
        )
    return normalized


def normalize_schedule_threshold_minutes(value: int | None) -> int:
    if value is None:
        return AUTOMATION_DEFAULT_THRESHOLD_MINUTES
    if value < 0:
        raise AutomationValidationError(
            "Schedule threshold must be greater than or equal to 0",
            code="invalid_schedule_threshold",
        )
    if value > AUTOMATION_MAX_THRESHOLD_MINUTES:
        raise AutomationValidationError(
            f"Schedule threshold cannot exceed {AUTOMATION_MAX_THRESHOLD_MINUTES} minutes",
            code="invalid_schedule_threshold",
        )
    return value


def compute_latest_due_slot_utc(
    now_utc: datetime,
    *,
    schedule_time: str,
    timezone_name: str,
    schedule_days: list[str],
) -> datetime:
    normalized_now = _normalize_now_utc(now_utc)
    hour, minute = parse_schedule_time_hhmm(schedule_time)
    timezone = ZoneInfo(resolve_schedule_timezone_name(timezone_name))
    allowed_days = set(normalize_schedule_days(schedule_days))

    local_now = normalized_now.replace(tzinfo=UTC).astimezone(timezone)
    local_candidate = datetime(
        local_now.year,
        local_now.month,
        local_now.day,
        hour,
        minute,
        tzinfo=timezone,
    )
    if local_candidate > local_now:
        local_candidate -= timedelta(days=1)

    for _ in range(8):
        if _local_weekday_code(local_candidate) in allowed_days:
            return local_candidate.astimezone(UTC).replace(tzinfo=None)
        local_candidate -= timedelta(days=1)

    raise RuntimeError("Failed to resolve latest due slot for automation schedule")


def compute_due_slot_for_current_local_day_utc(
    now_utc: datetime,
    *,
    schedule_time: str,
    timezone_name: str,
    schedule_days: list[str],
) -> datetime | None:
    normalized_now = _normalize_now_utc(now_utc)
    hour, minute = parse_schedule_time_hhmm(schedule_time)
    timezone = ZoneInfo(resolve_schedule_timezone_name(timezone_name))
    allowed_days = set(normalize_schedule_days(schedule_days))

    local_now = normalized_now.replace(tzinfo=UTC).astimezone(timezone)
    local_due = datetime(
        local_now.year,
        local_now.month,
        local_now.day,
        hour,
        minute,
        tzinfo=timezone,
    )
    if _local_weekday_code(local_due) not in allowed_days:
        return None
    if local_due > local_now:
        return None
    return local_due.astimezone(UTC).replace(tzinfo=None)


def compute_next_run_utc(
    now_utc: datetime,
    *,
    schedule_time: str,
    timezone_name: str,
    schedule_days: list[str],
) -> datetime:
    normalized_now = _normalize_now_utc(now_utc)
    hour, minute = parse_schedule_time_hhmm(schedule_time)
    timezone = ZoneInfo(resolve_schedule_timezone_name(timezone_name))
    allowed_days = set(normalize_schedule_days(schedule_days))

    local_now = normalized_now.replace(tzinfo=UTC).astimezone(timezone)
    local_candidate = datetime(
        local_now.year,
        local_now.month,
        local_now.day,
        hour,
        minute,
        tzinfo=timezone,
    )
    if local_candidate <= local_now:
        local_candidate += timedelta(days=1)

    for _ in range(8):
        if _local_weekday_code(local_candidate) in allowed_days:
            return local_candidate.astimezone(UTC).replace(tzinfo=None)
        local_candidate += timedelta(days=1)

    raise RuntimeError("Failed to resolve next run for automation schedule")


def _normalize_now_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _local_weekday_code(value: datetime) -> str:
    return _WEEKDAY_INDEX_TO_CODE[value.weekday()]


def resolve_schedule_timezone_name(value: str) -> str:
    normalized = value.strip()
    if normalized.lower() in AUTOMATION_SERVER_DEFAULT_TIMEZONE_ALIASES:
        return _resolve_server_timezone_name()
    return normalized


def _resolve_server_timezone_name() -> str:
    candidates: list[str] = []

    env_timezone = os.getenv("TZ", "").strip()
    if env_timezone:
        candidates.append(env_timezone)

    local_timezone = datetime.now().astimezone().tzinfo
    local_key = getattr(local_timezone, "key", None)
    if isinstance(local_key, str) and local_key.strip():
        candidates.append(local_key.strip())

    if local_timezone is not None:
        local_label = str(local_timezone).strip()
        if local_label:
            candidates.append(local_label)

    candidates.append("UTC")

    for candidate in candidates:
        try:
            ZoneInfo(candidate)
            return candidate
        except ZoneInfoNotFoundError:
            continue

    return "UTC"


class AutomationsService:
    def __init__(
        self,
        repository: AutomationsRepository,
        accounts_repository: AccountsRepository,
        request_logs_repository: RequestLogsRepository | None = None,
    ) -> None:
        self._repository = repository
        self._accounts_repository = accounts_repository
        self._request_logs_repository = request_logs_repository
        self._auth_manager = AuthManager(accounts_repository)
        self._encryptor = TokenEncryptor()

    async def list_jobs(self, *, now_utc: datetime | None = None) -> list[AutomationJobData]:
        now = now_utc or utcnow()
        jobs = await self._repository.list_jobs()
        latest_runs = await self._repository.get_latest_runs_by_job_ids([job.id for job in jobs])
        latest_run_data = await self._enrich_runs_with_progress(
            list(latest_runs.values()),
            apply_cycle_terminal_overrides=True,
        )
        latest_run_data_by_job_id = {run.job_id: run for run in latest_run_data}
        return [self._to_job_data(job, latest_run_data_by_job_id.get(job.id), now_utc=now) for job in jobs]

    async def list_jobs_page(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        account_ids: list[str] | None = None,
        models: list[str] | None = None,
        statuses: list[str] | None = None,
        schedule_types: list[str] | None = None,
        now_utc: datetime | None = None,
    ) -> AutomationJobsPageData:
        now = now_utc or utcnow()
        jobs, total = await self._repository.list_jobs_page(
            limit=limit,
            offset=offset,
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=_normalize_job_status_filters(statuses),
            schedule_types=schedule_types,
        )
        latest_runs = await self._repository.get_latest_runs_by_job_ids([job.id for job in jobs])
        latest_run_data = await self._enrich_runs_with_progress(
            list(latest_runs.values()),
            apply_cycle_terminal_overrides=True,
        )
        latest_run_data_by_job_id = {run.job_id: run for run in latest_run_data}
        items = [self._to_job_data(job, latest_run_data_by_job_id.get(job.id), now_utc=now) for job in jobs]
        return AutomationJobsPageData(items=items, total=total, has_more=offset + limit < total)

    async def list_job_filter_options(
        self,
        *,
        search: str | None = None,
        account_ids: list[str] | None = None,
        models: list[str] | None = None,
        statuses: list[str] | None = None,
        schedule_types: list[str] | None = None,
    ) -> AutomationJobFilterOptionsData:
        options = await self._repository.list_job_filter_options(
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=_normalize_job_status_filters(statuses),
            schedule_types=schedule_types,
        )
        accounts = await self._accounts_repository.list_accounts()
        available_account_ids = sorted(
            {
                *options.account_ids,
                *(account.id for account in accounts),
            }
        )
        return AutomationJobFilterOptionsData(
            account_ids=available_account_ids,
            models=options.models,
            statuses=options.statuses,
            schedule_types=options.schedule_types,
        )

    async def create_job(
        self, payload: AutomationJobCreateInput, *, now_utc: datetime | None = None
    ) -> AutomationJobData:
        normalized = await self._normalize_create_input(payload)
        prompt = normalized.prompt if normalized.prompt is not None else DEFAULT_AUTOMATION_PROMPT
        record = await self._repository.create_job(
            name=normalized.name,
            enabled=normalized.enabled,
            include_paused_accounts=normalized.include_paused_accounts,
            schedule_type=normalized.schedule_type,
            schedule_time=normalized.schedule_time,
            schedule_timezone=normalized.schedule_timezone,
            schedule_days=normalized.schedule_days or list(AUTOMATION_DEFAULT_WEEKDAYS),
            schedule_threshold_minutes=normalized.schedule_threshold_minutes
            if normalized.schedule_threshold_minutes is not None
            else AUTOMATION_DEFAULT_THRESHOLD_MINUTES,
            model=normalized.model,
            reasoning_effort=normalized.reasoning_effort,
            prompt=prompt,
            account_ids=normalized.account_ids,
        )
        now = now_utc or utcnow()
        return self._to_job_data(record, None, now_utc=now)

    async def update_job(
        self,
        job_id: str,
        payload: AutomationJobUpdateInput,
        *,
        now_utc: datetime | None = None,
    ) -> AutomationJobData:
        existing = await self._repository.get_job(job_id)
        if existing is None:
            raise AutomationNotFoundError(job_id)
        normalized = await self._normalize_update_input(payload, existing=existing)
        updated = await self._repository.update_job(
            job_id,
            name=normalized.name,
            enabled=normalized.enabled,
            include_paused_accounts=normalized.include_paused_accounts,
            schedule_type=normalized.schedule_type,
            schedule_time=normalized.schedule_time,
            schedule_timezone=normalized.schedule_timezone,
            schedule_days=normalized.schedule_days,
            schedule_threshold_minutes=normalized.schedule_threshold_minutes,
            model=normalized.model,
            reasoning_effort=normalized.reasoning_effort,
            reasoning_effort_set=normalized.reasoning_effort_set,
            prompt=normalized.prompt,
            account_ids=normalized.account_ids,
        )
        if updated is None:
            raise AutomationNotFoundError(job_id)
        latest_runs = await self._repository.get_latest_runs_by_job_ids([job_id])
        latest_run_data = await self._enrich_runs_with_progress(
            list(latest_runs.values()),
            apply_cycle_terminal_overrides=True,
        )
        latest_run_data_by_job_id = {run.job_id: run for run in latest_run_data}
        now = now_utc or utcnow()
        return self._to_job_data(updated, latest_run_data_by_job_id.get(job_id), now_utc=now)

    async def delete_job(self, job_id: str) -> bool:
        return await self._repository.delete_job(job_id)

    async def list_runs(self, job_id: str, *, limit: int = 20) -> list[AutomationRunData]:
        job = await self._repository.get_job(job_id)
        if job is None:
            raise AutomationNotFoundError(job_id)
        runs = await self._repository.list_runs(job_id, limit=limit)
        items = await self._enrich_runs_with_progress(runs)
        return sorted(items, key=lambda entry: (entry.started_at, entry.id), reverse=True)

    async def list_runs_page(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        account_ids: list[str] | None = None,
        models: list[str] | None = None,
        statuses: list[str] | None = None,
        triggers: list[str] | None = None,
        job_ids: list[str] | None = None,
    ) -> AutomationRunsPageData:
        runs, total = await self._repository.list_run_cycles_page(
            limit=limit,
            offset=offset,
            now_utc=utcnow(),
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=_normalize_run_status_filters(statuses),
            triggers=_normalize_run_trigger_filters(triggers),
            job_ids=job_ids,
        )
        items = await self._enrich_runs_with_progress(runs, apply_cycle_terminal_overrides=True)
        return AutomationRunsPageData(items=items, total=total, has_more=offset + limit < total)

    async def list_run_filter_options(
        self,
        *,
        search: str | None = None,
        account_ids: list[str] | None = None,
        models: list[str] | None = None,
        statuses: list[str] | None = None,
        triggers: list[str] | None = None,
        job_ids: list[str] | None = None,
    ) -> AutomationRunFilterOptionsData:
        normalized_statuses = _normalize_run_status_filters(statuses)
        normalized_triggers = _normalize_run_trigger_filters(triggers)
        options = await self._repository.list_run_filter_options(
            now_utc=utcnow(),
            search=search,
            account_ids=account_ids,
            models=models,
            statuses=normalized_statuses,
            triggers=normalized_triggers,
            job_ids=job_ids,
        )
        has_active_filters = bool(
            (search or "").strip() or account_ids or models or normalized_statuses or normalized_triggers or job_ids
        )
        if has_active_filters:
            available_account_ids = sorted(options.account_ids)
        else:
            accounts = await self._accounts_repository.list_accounts()
            available_account_ids = sorted(
                {
                    *options.account_ids,
                    *(account.id for account in accounts),
                }
            )
        return AutomationRunFilterOptionsData(
            account_ids=available_account_ids,
            models=options.models,
            statuses=_all_run_statuses(),
            triggers=_all_run_triggers(),
        )

    async def get_run_details(self, run_id: str) -> AutomationRunDetailsData:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise AutomationNotFoundError(run_id)
        jobs_by_id = await self._repository.get_jobs_by_ids([run.job_id])
        job = jobs_by_id.get(run.job_id)
        if job is None:
            raise AutomationNotFoundError(run.job_id)
        summary = await self._build_cycle_summary_for_run(
            run=run,
            job=job,
            now_utc=utcnow(),
        )
        return AutomationRunDetailsData(
            run=self._to_run_data(run, summary=summary, apply_cycle_terminal_overrides=True),
            accounts=summary.accounts,
            total_accounts=summary.total_accounts,
            completed_accounts=summary.completed_accounts,
            pending_accounts=summary.pending_accounts,
        )

    async def run_now(self, job_id: str, *, now_utc: datetime | None = None) -> AutomationRunData:
        job = await self._repository.get_job(job_id)
        if job is None:
            raise AutomationNotFoundError(job_id)
        now = now_utc or utcnow()
        cycle_id = uuid4().hex
        cycle_key = _manual_cycle_key(job.id, cycle_id)
        account_ids = await self._resolve_job_account_ids_for_dispatch(job)
        threshold = max(0, job.schedule_threshold_minutes)
        cycle_window_end = now + timedelta(minutes=threshold)
        dispatch_plan = _build_dispatch_plan(
            job_id=job.id,
            due_slot=now,
            account_ids=account_ids,
            threshold_minutes=threshold,
        )
        cycle = await self._repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger=AUTOMATION_RUN_TRIGGER_MANUAL,
            cycle_expected_accounts=len(dispatch_plan),
            cycle_window_end=cycle_window_end,
            accounts=dispatch_plan,
        )
        if not cycle.accounts:
            slot_key = f"manual:{job.id}:{cycle_id}:none"
            claim = await self._repository.claim_run(
                job_id=job.id,
                trigger=AUTOMATION_RUN_TRIGGER_MANUAL,
                slot_key=slot_key,
                cycle_key=cycle_key,
                cycle_expected_accounts=cycle.cycle_expected_accounts,
                cycle_window_end=cycle.cycle_window_end,
                scheduled_for=now,
                started_at=now,
            )
            if claim is None:
                raise RuntimeError("Failed to claim manual automation run")
            return await self._execute_claimed_run(job, claim)

        representative_claim: AutomationRunRecord | None = None
        for cycle_account in cycle.accounts:
            slot_key = _manual_slot_key(job.id, cycle_id, cycle_account.account_id)
            claim = await self._repository.claim_run(
                job_id=job.id,
                trigger=AUTOMATION_RUN_TRIGGER_MANUAL,
                slot_key=slot_key,
                cycle_key=cycle_key,
                cycle_expected_accounts=cycle.cycle_expected_accounts,
                cycle_window_end=cycle.cycle_window_end,
                scheduled_for=cycle_account.scheduled_for,
                started_at=now,
                account_id=cycle_account.account_id,
            )
            if claim is None:
                continue
            representative_claim = claim
        if representative_claim is not None:
            representative_run = await self._repository.get_run(representative_claim.id)
            if representative_run is None:
                representative_run = representative_claim
            summary = await self._build_cycle_summary_for_run(
                run=representative_run,
                job=job,
                now_utc=now,
            )
            return self._to_run_data(
                representative_run,
                summary=summary,
                apply_cycle_terminal_overrides=True,
            )
        raise RuntimeError("Failed to claim manual automation run")

    async def run_due_jobs(self, *, now_utc: datetime | None = None) -> int:
        now = now_utc or utcnow()
        executed = await self._run_due_manual_runs(now_utc=now)
        jobs = await self._repository.list_enabled_jobs()
        for job in jobs:
            due_slot = compute_due_slot_for_current_local_day_utc(
                now,
                schedule_time=job.schedule_time,
                timezone_name=job.schedule_timezone,
                schedule_days=job.schedule_days,
            )
            if due_slot is None:
                continue
            cycle_key = _scheduled_cycle_key(job.id, due_slot)
            cycle = await self._repository.get_run_cycle(cycle_key=cycle_key)
            if cycle is None and due_slot < _normalize_now_utc(job.updated_at):
                continue
            if cycle is None:
                cycle = await self._get_or_create_scheduled_cycle(job=job, due_slot=due_slot)
            existing_cycle_runs = await self._repository.list_runs_for_cycle_key(cycle_key=cycle_key)
            existing_cycle_account_ids = {
                cycle_run.account_id for cycle_run in existing_cycle_runs if cycle_run.account_id is not None
            }
            for cycle_account in cycle.accounts:
                if cycle_account.account_id in existing_cycle_account_ids:
                    continue
                if cycle_account.scheduled_for > now:
                    continue
                slot_key = _scheduled_slot_key(
                    job.id,
                    account_id=cycle_account.account_id,
                    due_slot=due_slot,
                )
                claim = await self._repository.claim_run(
                    job_id=job.id,
                    trigger=AUTOMATION_RUN_TRIGGER_SCHEDULED,
                    slot_key=slot_key,
                    cycle_key=cycle_key,
                    cycle_expected_accounts=cycle.cycle_expected_accounts,
                    cycle_window_end=cycle.cycle_window_end,
                    scheduled_for=cycle_account.scheduled_for,
                    started_at=now,
                    account_id=cycle_account.account_id,
                )
                if claim is None:
                    continue
                existing_cycle_account_ids.add(cycle_account.account_id)
                await self._execute_claimed_run(job, claim, forced_account_id=cycle_account.account_id)
                executed += 1
        return executed

    async def _run_due_manual_runs(self, *, now_utc: datetime) -> int:
        stale_started_before = now_utc - timedelta(seconds=_manual_run_execution_claim_timeout_seconds())
        due_runs = await self._repository.list_due_manual_runs(
            now_utc=now_utc,
            stale_started_before=stale_started_before,
        )
        if not due_runs:
            return 0
        jobs_by_id = await self._repository.get_jobs_by_ids([run.job_id for run in due_runs])
        executed = 0
        for run in due_runs:
            job = jobs_by_id.get(run.job_id)
            if job is None or run.account_id is None:
                continue
            claimed_started_at = max(
                utcnow(),
                run.started_at + timedelta(microseconds=1),
                run.scheduled_for + timedelta(microseconds=1),
            )
            claimed_run = await self._repository.claim_manual_run_execution(
                run.id,
                observed_started_at=run.started_at,
                claimed_started_at=claimed_started_at,
                stale_started_before=stale_started_before,
            )
            if claimed_run is None:
                continue
            await self._execute_claimed_run(job, claimed_run, forced_account_id=claimed_run.account_id)
            executed += 1
        return executed

    async def _get_or_create_scheduled_cycle(
        self,
        *,
        job: AutomationJobRecord,
        due_slot: datetime,
    ) -> AutomationRunCycleRecord:
        cycle_key = _scheduled_cycle_key(job.id, due_slot)
        existing_cycle = await self._repository.get_run_cycle(cycle_key=cycle_key)
        if existing_cycle is not None:
            return existing_cycle

        threshold = max(0, job.schedule_threshold_minutes)
        account_ids = await self._resolve_job_account_ids_for_dispatch(job)
        dispatch_plan = _build_dispatch_plan(
            job_id=job.id,
            due_slot=due_slot,
            account_ids=account_ids,
            threshold_minutes=threshold,
        )
        return await self._repository.create_run_cycle(
            cycle_key=cycle_key,
            job_id=job.id,
            trigger=AUTOMATION_RUN_TRIGGER_SCHEDULED,
            cycle_expected_accounts=len(dispatch_plan),
            cycle_window_end=due_slot + timedelta(minutes=threshold),
            accounts=dispatch_plan,
        )

    async def _execute_claimed_run(
        self,
        job: AutomationJobRecord,
        run: AutomationRunRecord,
        *,
        forced_account_id: str | None = None,
    ) -> AutomationRunData:
        had_prior_failures = False
        attempt_count = 0
        last_error_code: str | None = "no_available_accounts"
        last_error_message: str | None = "No available accounts configured for automation job"
        last_attempted_account_id: str | None = forced_account_id
        cached_accounts_by_id: dict[str, Account] | None = None
        if forced_account_id is not None:
            account_ids_to_try = [forced_account_id]
        else:
            account_ids_to_try = list(job.account_ids)
        if not account_ids_to_try:
            accounts = await self._accounts_repository.list_accounts()
            account_ids_to_try = [
                account.id
                for account in accounts
                if self._is_account_eligible_for_automation(
                    account,
                    include_paused_accounts=job.include_paused_accounts,
                )
            ]
            cached_accounts_by_id = {account.id: account for account in accounts}

        for account_id in account_ids_to_try:
            request_started_at: float | None = None
            if cached_accounts_by_id is None:
                account = await self._accounts_repository.get_by_id(account_id)
            else:
                account = cached_accounts_by_id.get(account_id)
            if account is None:
                had_prior_failures = True
                last_error_code = "account_not_found"
                last_error_message = f"Account '{account_id}' not found"
                continue
            if not self._is_account_eligible_for_automation(
                account,
                include_paused_accounts=job.include_paused_accounts,
            ):
                continue

            try:
                attempt_count += 1
                last_attempted_account_id = account_id
                account = await self._auth_manager.ensure_fresh(account)
                access_token = self._encryptor.decrypt(account.access_token_encrypted)
                ping_request = ResponsesCompactRequest(
                    model=job.model,
                    input=job.prompt,
                    instructions="Automation ping",
                    reasoning=ResponsesReasoning(effort=job.reasoning_effort) if job.reasoning_effort else None,
                )
                request_started_at = time.monotonic()
                compact_response = await core_compact_responses(
                    ping_request,
                    headers={},
                    access_token=access_token,
                    account_id=account.chatgpt_account_id,
                )
                latency_ms = _elapsed_ms(request_started_at)
                request_id = _automation_request_id(getattr(compact_response, "id", None), run.id, attempt_count)
                (
                    input_tokens,
                    output_tokens,
                    cached_input_tokens,
                    reasoning_tokens,
                    service_tier,
                ) = _extract_compact_usage_fields(compact_response)
                await self._write_request_log(
                    account_id=account.id,
                    request_id=request_id,
                    model=job.model,
                    reasoning_effort=job.reasoning_effort,
                    latency_ms=latency_ms,
                    status="success",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    reasoning_tokens=reasoning_tokens,
                    service_tier=service_tier,
                )
                status = AUTOMATION_RUN_STATUS_PARTIAL if had_prior_failures else AUTOMATION_RUN_STATUS_SUCCESS
                completed = await self._repository.complete_run(
                    run.id,
                    status=status,
                    finished_at=utcnow(),
                    account_id=account.id,
                    error_code=None,
                    error_message=None,
                    attempt_count=attempt_count,
                )
                return self._to_run_data(completed)
            except RefreshError as exc:
                had_prior_failures = True
                last_error_code = exc.code or "authentication_error"
                last_error_message = exc.message
                if forced_account_id is None and self._is_retryable_account_failure(last_error_code):
                    continue
                break
            except ProxyResponseError as exc:
                had_prior_failures = True
                error_code, error_message = _extract_proxy_error(exc)
                last_error_code = error_code
                last_error_message = error_message
                await self._write_request_log(
                    account_id=account.id,
                    request_id=_automation_request_id(None, run.id, attempt_count),
                    model=job.model,
                    reasoning_effort=job.reasoning_effort,
                    latency_ms=_elapsed_ms(request_started_at),
                    status="error",
                    error_code=error_code,
                    error_message=error_message,
                )
                if forced_account_id is None and self._is_retryable_account_failure(error_code):
                    continue
                break
            except Exception as exc:
                had_prior_failures = True
                last_error_code = "automation_ping_failed"
                last_error_message = str(exc) or "Automation ping failed"
                if request_started_at is not None:
                    await self._write_request_log(
                        account_id=account.id,
                        request_id=_automation_request_id(None, run.id, attempt_count),
                        model=job.model,
                        reasoning_effort=job.reasoning_effort,
                        latency_ms=_elapsed_ms(request_started_at),
                        status="error",
                        error_code=last_error_code,
                        error_message=last_error_message,
                    )
                continue

        completed = await self._repository.complete_run(
            run.id,
            status=AUTOMATION_RUN_STATUS_FAILED,
            finished_at=utcnow(),
            account_id=last_attempted_account_id,
            error_code=last_error_code,
            error_message=last_error_message,
            attempt_count=attempt_count,
        )
        return self._to_run_data(completed)

    async def _enrich_runs_with_progress(
        self,
        runs: list[AutomationRunRecord],
        *,
        apply_cycle_terminal_overrides: bool = False,
    ) -> list[AutomationRunData]:
        if not runs:
            return []
        jobs_by_id = await self._repository.get_jobs_by_ids([run.job_id for run in runs])
        cycle_cache: dict[str, _AutomationRunCycleSummary] = {}
        items: list[AutomationRunData] = []
        now = utcnow()
        for run in runs:
            job = jobs_by_id.get(run.job_id)
            summary = None
            if job is not None:
                summary = await self._build_cycle_summary_for_run(
                    run=run,
                    job=job,
                    now_utc=now,
                    cycle_cache=cycle_cache,
                )
            items.append(
                self._to_run_data(
                    run,
                    summary=summary,
                    apply_cycle_terminal_overrides=apply_cycle_terminal_overrides,
                )
            )
        return items

    async def _build_cycle_summary_for_run(
        self,
        *,
        run: AutomationRunRecord,
        job: AutomationJobRecord,
        now_utc: datetime,
        cycle_cache: dict[str, _AutomationRunCycleSummary] | None = None,
    ) -> _AutomationRunCycleSummary:
        if run.trigger == AUTOMATION_RUN_TRIGGER_SCHEDULED:
            return await self._build_scheduled_cycle_summary(
                run=run,
                job=job,
                now_utc=now_utc,
                cycle_cache=cycle_cache,
            )
        return await self._build_manual_cycle_summary(
            run=run,
            job=job,
            now_utc=now_utc,
            cycle_cache=cycle_cache,
        )

    async def _build_manual_cycle_summary(
        self,
        *,
        run: AutomationRunRecord,
        job: AutomationJobRecord,
        now_utc: datetime,
        cycle_cache: dict[str, _AutomationRunCycleSummary] | None = None,
    ) -> _AutomationRunCycleSummary:
        parsed = _parse_manual_cycle_key(run.slot_key)
        cycle_key = _normalize_legacy_manual_cycle_key(run.cycle_key)
        if parsed is not None:
            cycle_id, _slot_key_prefix = parsed
            cycle_key = _manual_cycle_key(job.id, cycle_id)
        if cycle_key is None:
            return _AutomationRunCycleSummary(
                cycle_key=f"manual:{run.id}",
                cycle_started_at=run.scheduled_for,
                cycle_finished_at=run.finished_at,
                effective_status=run.status,
                total_accounts=1 if run.account_id else 0,
                completed_accounts=1 if run.account_id else 0,
                pending_accounts=0,
                error_code=run.error_code,
                error_message=run.error_message,
                accounts=[
                    AutomationRunAccountStateData(
                        account_id=run.account_id,
                        status=run.status,
                        run_id=run.id,
                        scheduled_for=run.scheduled_for,
                        started_at=run.started_at,
                        finished_at=run.finished_at,
                        error_code=run.error_code,
                        error_message=run.error_message,
                    )
                ]
                if run.account_id
                else [],
            )

        if cycle_cache is not None and cycle_key in cycle_cache:
            return cycle_cache[cycle_key]

        cycle_runs = await self._repository.list_runs_for_cycle_key(cycle_key=cycle_key)
        if run.account_id and run.account_id not in {cycle_run.account_id for cycle_run in cycle_runs}:
            normalized_run_cycle_key = _normalize_legacy_manual_cycle_key(run.cycle_key)
            if normalized_run_cycle_key == cycle_key:
                cycle_runs = [run, *cycle_runs]
        cycle = await self._repository.get_run_cycle(cycle_key=cycle_key)
        latest_run_by_account_id: dict[str, AutomationRunRecord] = {}
        for cycle_run in cycle_runs:
            if not cycle_run.account_id:
                continue
            if cycle_run.account_id in latest_run_by_account_id:
                continue
            latest_run_by_account_id[cycle_run.account_id] = cycle_run

        if cycle is not None:
            expected_account_ids = [entry.account_id for entry in cycle.accounts]
            scheduled_for_by_account_id = {entry.account_id: entry.scheduled_for for entry in cycle.accounts}
        else:
            expected_account_ids = []
            seen_account_ids: set[str] = set()
            for cycle_run in sorted(
                cycle_runs,
                key=lambda entry: (entry.scheduled_for, entry.account_id or "", entry.id),
            ):
                account_id = cycle_run.account_id
                if not account_id:
                    continue
                if account_id in seen_account_ids:
                    continue
                seen_account_ids.add(account_id)
                expected_account_ids.append(account_id)

            if not expected_account_ids and run.account_id:
                expected_account_ids = [run.account_id]
            scheduled_for_by_account_id = {
                account_id: entry.scheduled_for for account_id, entry in latest_run_by_account_id.items()
            }
        cycle_started_at = min(
            scheduled_for_by_account_id.values(),
            default=min((entry.scheduled_for for entry in cycle_runs), default=run.scheduled_for),
        )
        expected_set = set(expected_account_ids)
        observed_only = [account_id for account_id in latest_run_by_account_id if account_id not in expected_set]
        all_account_ids = [*expected_account_ids, *observed_only]

        account_states: list[AutomationRunAccountStateData] = []
        for account_id in all_account_ids:
            observed_run = latest_run_by_account_id.get(account_id)
            if observed_run is None:
                account_states.append(
                    AutomationRunAccountStateData(
                        account_id=account_id,
                        status="pending",
                        run_id=None,
                        scheduled_for=scheduled_for_by_account_id.get(account_id),
                        started_at=None,
                        finished_at=None,
                        error_code=None,
                        error_message=None,
                    )
                )
                continue
            account_status = observed_run.status
            if observed_run.status == AUTOMATION_RUN_STATUS_RUNNING and observed_run.scheduled_for > now_utc:
                account_status = "pending"
            account_states.append(
                AutomationRunAccountStateData(
                    account_id=account_id,
                    status=account_status,
                    run_id=observed_run.id,
                    scheduled_for=observed_run.scheduled_for,
                    started_at=observed_run.started_at,
                    finished_at=observed_run.finished_at,
                    error_code=observed_run.error_code,
                    error_message=observed_run.error_message,
                )
            )

        expected_accounts_hint = (
            cycle.cycle_expected_accounts
            if cycle is not None
            else max(
                [entry.cycle_expected_accounts or 0 for entry in cycle_runs],
                default=run.cycle_expected_accounts or 0,
            )
        )
        total_accounts = max(len(all_account_ids), expected_accounts_hint)
        completed_accounts = sum(
            1
            for account_id in all_account_ids
            if (entry := latest_run_by_account_id.get(account_id)) is not None
            and entry.status != AUTOMATION_RUN_STATUS_RUNNING
        )
        pending_accounts = max(0, total_accounts - completed_accounts)
        status_counts = {
            "success": 0,
            "failed": 0,
            "partial": 0,
            "running": 0,
        }
        for entry in latest_run_by_account_id.values():
            if entry.status in status_counts:
                status_counts[entry.status] += 1
        effective_status = _resolve_effective_status(
            pending_accounts=pending_accounts,
            completed_accounts=completed_accounts,
            success_count=status_counts["success"],
            failed_count=status_counts["failed"],
            partial_count=status_counts["partial"],
            running_count=status_counts["running"],
            fallback_status=run.status,
            now_utc=now_utc,
            window_end_utc=(
                cycle.cycle_window_end
                if cycle is not None
                else max(
                    [entry.cycle_window_end or entry.scheduled_for for entry in cycle_runs],
                    default=run.cycle_window_end or run.scheduled_for,
                )
            )
            or run.scheduled_for,
        )
        cycle_finished_at = _resolve_cycle_finished_at(account_states, pending_accounts=pending_accounts)
        error_code, error_message = _resolve_cycle_error_summary(account_states)
        summary = _AutomationRunCycleSummary(
            cycle_key=cycle_key,
            cycle_started_at=cycle_started_at,
            cycle_finished_at=cycle_finished_at,
            effective_status=effective_status,
            total_accounts=total_accounts,
            completed_accounts=completed_accounts,
            pending_accounts=pending_accounts,
            error_code=error_code,
            error_message=error_message,
            accounts=account_states,
        )
        if cycle_cache is not None:
            cycle_cache[cycle_key] = summary
        return summary

    async def _build_scheduled_cycle_summary(
        self,
        *,
        run: AutomationRunRecord,
        job: AutomationJobRecord,
        now_utc: datetime,
        cycle_cache: dict[str, _AutomationRunCycleSummary] | None = None,
    ) -> _AutomationRunCycleSummary:
        due_slot = compute_latest_due_slot_utc(
            run.scheduled_for,
            schedule_time=job.schedule_time,
            timezone_name=job.schedule_timezone,
            schedule_days=job.schedule_days,
        )
        cycle_key = (
            run.cycle_key.strip() if run.cycle_key and run.cycle_key.strip() else _scheduled_cycle_key(job.id, due_slot)
        )
        if cycle_cache is not None and cycle_key in cycle_cache:
            return cycle_cache[cycle_key]

        cycle = await self._repository.get_run_cycle(cycle_key=cycle_key)
        cycle_runs = await self._repository.list_runs_for_cycle_key(cycle_key=cycle_key)

        latest_run_by_account_id: dict[str, AutomationRunRecord] = {}
        for cycle_run in cycle_runs:
            if not cycle_run.account_id:
                continue
            if cycle_run.account_id in latest_run_by_account_id:
                continue
            latest_run_by_account_id[cycle_run.account_id] = cycle_run

        if cycle is not None:
            expected_account_ids = [entry.account_id for entry in cycle.accounts]
            scheduled_for_by_account_id = {entry.account_id: entry.scheduled_for for entry in cycle.accounts}
        else:
            expected_account_ids = []
            scheduled_for_by_account_id = {}
            for cycle_run in sorted(
                (entry for entry in cycle_runs if entry.account_id),
                key=lambda entry: (entry.scheduled_for, entry.account_id or "", entry.id),
            ):
                account_id = cycle_run.account_id
                if account_id is None or account_id in scheduled_for_by_account_id:
                    continue
                scheduled_for_by_account_id[account_id] = cycle_run.scheduled_for
                expected_account_ids.append(account_id)
        if not expected_account_ids and run.account_id:
            expected_account_ids = [run.account_id]
            scheduled_for_by_account_id[run.account_id] = run.scheduled_for
        observed_account_ids = [
            account_id for account_id in latest_run_by_account_id if account_id not in expected_account_ids
        ]
        all_account_ids = [*expected_account_ids, *observed_account_ids]

        account_states: list[AutomationRunAccountStateData] = []
        for account_id in all_account_ids:
            observed_run = latest_run_by_account_id.get(account_id)
            if observed_run is None:
                account_states.append(
                    AutomationRunAccountStateData(
                        account_id=account_id,
                        status="pending",
                        run_id=None,
                        scheduled_for=scheduled_for_by_account_id.get(account_id),
                        started_at=None,
                        finished_at=None,
                        error_code=None,
                        error_message=None,
                    )
                )
                continue
            account_states.append(
                AutomationRunAccountStateData(
                    account_id=account_id,
                    status=observed_run.status,
                    run_id=observed_run.id,
                    scheduled_for=observed_run.scheduled_for,
                    started_at=observed_run.started_at,
                    finished_at=observed_run.finished_at,
                    error_code=observed_run.error_code,
                    error_message=observed_run.error_message,
                )
            )

        expected_accounts_hint = (
            cycle.cycle_expected_accounts
            if cycle is not None
            else max(
                [entry.cycle_expected_accounts or 0 for entry in cycle_runs],
                default=run.cycle_expected_accounts or 0,
            )
        )
        total_accounts = max(len(all_account_ids), expected_accounts_hint)
        completed_accounts = sum(
            1
            for account_id in all_account_ids
            if (entry := latest_run_by_account_id.get(account_id)) is not None
            and entry.status != AUTOMATION_RUN_STATUS_RUNNING
        )
        pending_accounts = max(0, total_accounts - completed_accounts)
        status_counts = {
            "success": 0,
            "failed": 0,
            "partial": 0,
            "running": 0,
        }
        for entry in latest_run_by_account_id.values():
            if entry.status in status_counts:
                status_counts[entry.status] += 1
        window_end = (
            cycle.cycle_window_end
            if cycle is not None
            else max(
                [entry.cycle_window_end or entry.scheduled_for for entry in cycle_runs],
                default=run.cycle_window_end or run.scheduled_for,
            )
        ) or run.scheduled_for
        effective_status = _resolve_effective_status(
            pending_accounts=pending_accounts,
            completed_accounts=completed_accounts,
            success_count=status_counts["success"],
            failed_count=status_counts["failed"],
            partial_count=status_counts["partial"],
            running_count=status_counts["running"],
            fallback_status=run.status,
            now_utc=now_utc,
            window_end_utc=window_end,
        )
        cycle_finished_at = _resolve_cycle_finished_at(account_states, pending_accounts=pending_accounts)
        error_code, error_message = _resolve_cycle_error_summary(account_states)
        summary = _AutomationRunCycleSummary(
            cycle_key=cycle_key,
            cycle_started_at=max((entry.started_at for entry in cycle_runs), default=run.started_at),
            cycle_finished_at=cycle_finished_at,
            effective_status=effective_status,
            total_accounts=total_accounts,
            completed_accounts=completed_accounts,
            pending_accounts=pending_accounts,
            error_code=error_code,
            error_message=error_message,
            accounts=account_states,
        )
        if cycle_cache is not None:
            cycle_cache[cycle_key] = summary
        return summary

    async def _normalize_create_input(self, payload: AutomationJobCreateInput) -> AutomationJobCreateInput:
        name = _normalize_non_empty(payload.name, field_label="name", code="invalid_name")
        model = _normalize_non_empty(payload.model, field_label="model", code="invalid_model")
        schedule_type = _normalize_schedule_type(payload.schedule_type)
        schedule_time = normalize_schedule_time(payload.schedule_time)
        schedule_timezone = validate_timezone(payload.schedule_timezone)
        schedule_days = normalize_schedule_days(payload.schedule_days)
        schedule_threshold_minutes = normalize_schedule_threshold_minutes(payload.schedule_threshold_minutes)
        reasoning_effort = _normalize_reasoning_effort(payload.reasoning_effort, model_slug=model)
        prompt = _normalize_prompt(payload.prompt)
        account_ids = await self._normalize_account_ids(
            payload.account_ids,
            include_paused_accounts=payload.include_paused_accounts,
        )
        return AutomationJobCreateInput(
            name=name,
            enabled=payload.enabled,
            include_paused_accounts=payload.include_paused_accounts,
            schedule_type=schedule_type,
            schedule_time=schedule_time,
            schedule_timezone=schedule_timezone,
            schedule_days=schedule_days,
            schedule_threshold_minutes=schedule_threshold_minutes,
            model=model,
            reasoning_effort=reasoning_effort,
            prompt=prompt,
            account_ids=account_ids,
        )

    async def _normalize_update_input(
        self,
        payload: AutomationJobUpdateInput,
        *,
        existing: AutomationJobRecord,
    ) -> AutomationJobUpdateInput:
        if payload.name is None:
            name = None
        else:
            name = _normalize_non_empty(payload.name, field_label="name", code="invalid_name")
        if payload.model is None:
            model = None
        else:
            model = _normalize_non_empty(payload.model, field_label="model", code="invalid_model")
        model_for_reasoning = model if model is not None else existing.model
        if payload.reasoning_effort_set:
            reasoning_effort = _normalize_reasoning_effort(payload.reasoning_effort, model_slug=model_for_reasoning)
        else:
            reasoning_effort = None
        if payload.schedule_type is None:
            schedule_type = None
        else:
            schedule_type = _normalize_schedule_type(payload.schedule_type)
        if payload.schedule_time is None:
            schedule_time = None
        else:
            schedule_time = normalize_schedule_time(payload.schedule_time)
        if payload.schedule_timezone is None:
            schedule_timezone = None
        else:
            schedule_timezone = validate_timezone(payload.schedule_timezone)
        if payload.schedule_days is None:
            schedule_days = None
        else:
            schedule_days = normalize_schedule_days(payload.schedule_days)
        if payload.schedule_threshold_minutes is None:
            schedule_threshold_minutes = None
        else:
            schedule_threshold_minutes = normalize_schedule_threshold_minutes(payload.schedule_threshold_minutes)
        if payload.prompt is None:
            prompt = None
        else:
            prompt = _normalize_prompt(payload.prompt)
        if payload.include_paused_accounts is None:
            include_paused_accounts = None
        else:
            include_paused_accounts = payload.include_paused_accounts
        if payload.account_ids is None:
            account_ids = None
        else:
            account_ids = await self._normalize_account_ids(
                payload.account_ids,
                include_paused_accounts=(
                    payload.include_paused_accounts
                    if payload.include_paused_accounts is not None
                    else existing.include_paused_accounts
                ),
            )

        if (
            name is None
            and payload.enabled is None
            and include_paused_accounts is None
            and schedule_type is None
            and schedule_time is None
            and schedule_timezone is None
            and schedule_days is None
            and schedule_threshold_minutes is None
            and model is None
            and not payload.reasoning_effort_set
            and prompt is None
            and account_ids is None
        ):
            return AutomationJobUpdateInput(
                name=existing.name,
                enabled=existing.enabled,
                include_paused_accounts=existing.include_paused_accounts,
                schedule_type=existing.schedule_type,
                schedule_time=existing.schedule_time,
                schedule_timezone=existing.schedule_timezone,
                schedule_days=existing.schedule_days,
                schedule_threshold_minutes=existing.schedule_threshold_minutes,
                model=existing.model,
                reasoning_effort=existing.reasoning_effort,
                reasoning_effort_set=True,
                prompt=existing.prompt,
                account_ids=existing.account_ids,
            )

        return AutomationJobUpdateInput(
            name=name,
            enabled=payload.enabled,
            include_paused_accounts=include_paused_accounts,
            schedule_type=schedule_type,
            schedule_time=schedule_time,
            schedule_timezone=schedule_timezone,
            schedule_days=schedule_days,
            schedule_threshold_minutes=schedule_threshold_minutes,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_effort_set=payload.reasoning_effort_set,
            prompt=prompt,
            account_ids=account_ids,
        )

    async def _normalize_account_ids(
        self,
        account_ids: list[str],
        *,
        include_paused_accounts: bool = False,
    ) -> list[str]:
        normalized = [account_id.strip() for account_id in account_ids if account_id.strip()]
        if not normalized:
            accounts = await self._accounts_repository.list_accounts()
            await self._reactivate_accounts_if_reset_elapsed(accounts, now_utc=utcnow())
            has_eligible_accounts = any(
                self._is_account_eligible_for_automation(
                    account,
                    include_paused_accounts=include_paused_accounts,
                )
                for account in accounts
            )
            if not has_eligible_accounts:
                raise AutomationValidationError(
                    "No eligible accounts available for this automation",
                    code="invalid_account_ids",
                )
            return []
        deduped = list(dict.fromkeys(normalized))
        existing = await self._repository.list_existing_account_ids(deduped)
        missing = [account_id for account_id in deduped if account_id not in existing]
        if missing:
            raise AutomationValidationError(
                f"Unknown account IDs: {', '.join(missing)}",
                code="invalid_account_ids",
            )
        return deduped

    def _to_job_data(
        self,
        job: AutomationJobRecord,
        last_run: AutomationRunData | None,
        *,
        now_utc: datetime,
    ) -> AutomationJobData:
        next_run_at = None
        if job.enabled:
            next_run_at = compute_next_run_utc(
                now_utc,
                schedule_time=job.schedule_time,
                timezone_name=job.schedule_timezone,
                schedule_days=job.schedule_days,
            )
        return AutomationJobData(
            id=job.id,
            name=job.name,
            enabled=job.enabled,
            include_paused_accounts=job.include_paused_accounts,
            schedule=AutomationScheduleData(
                type=job.schedule_type,
                time=job.schedule_time,
                timezone=job.schedule_timezone,
                days=job.schedule_days,
                threshold_minutes=job.schedule_threshold_minutes,
            ),
            model=job.model,
            reasoning_effort=job.reasoning_effort,
            prompt=job.prompt,
            account_ids=job.account_ids,
            next_run_at=next_run_at,
            last_run=last_run,
        )

    async def _resolve_job_account_ids_for_dispatch(self, job: AutomationJobRecord) -> list[str]:
        now_utc = utcnow()
        account_ids = list(job.account_ids)
        accounts = await self._accounts_repository.list_accounts()
        await self._reactivate_accounts_if_reset_elapsed(accounts, now_utc=now_utc)
        accounts_by_id = {account.id: account for account in accounts}
        candidate_accounts: list[Account]
        if account_ids:
            candidate_accounts = [
                accounts_by_id[account_id] for account_id in account_ids if account_id in accounts_by_id
            ]
        else:
            candidate_accounts = accounts
        return [
            account.id
            for account in candidate_accounts
            if self._is_account_eligible_for_automation(
                account,
                include_paused_accounts=job.include_paused_accounts,
            )
        ]

    async def _reactivate_accounts_if_reset_elapsed(
        self,
        accounts: list[Account],
        *,
        now_utc: datetime,
    ) -> None:
        now_epoch = naive_utc_to_epoch(now_utc)
        for account in accounts:
            if account.status not in {AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED}:
                continue
            if account.reset_at is None or account.reset_at > now_epoch:
                continue
            updated = await self._accounts_repository.update_status_if_current(
                account.id,
                AccountStatus.ACTIVE,
                None,
                None,
                blocked_at=None,
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
            )
            if updated:
                account.status = AccountStatus.ACTIVE
                account.deactivation_reason = None
                account.reset_at = None
                account.blocked_at = None

    @staticmethod
    def _to_run_data(
        run: AutomationRunRecord,
        *,
        summary: _AutomationRunCycleSummary | None = None,
        apply_cycle_terminal_overrides: bool = False,
    ) -> AutomationRunData:
        cycle_started_at = (
            summary.cycle_started_at if summary is not None and run.trigger == AUTOMATION_RUN_TRIGGER_MANUAL else None
        )
        cycle_finished_at = (
            summary.cycle_finished_at if summary is not None and apply_cycle_terminal_overrides else None
        )
        error_code = summary.error_code if summary is not None and apply_cycle_terminal_overrides else run.error_code
        error_message = (
            summary.error_message if summary is not None and apply_cycle_terminal_overrides else run.error_message
        )
        scheduled_for = cycle_started_at or run.scheduled_for
        started_at = cycle_started_at or run.started_at
        return AutomationRunData(
            id=run.id,
            job_id=run.job_id,
            job_name=run.job_name,
            model=run.model,
            reasoning_effort=run.reasoning_effort,
            trigger=run.trigger,
            status=run.status,
            scheduled_for=scheduled_for,
            started_at=started_at,
            finished_at=cycle_finished_at if apply_cycle_terminal_overrides else run.finished_at,
            account_id=run.account_id,
            error_code=error_code,
            error_message=error_message,
            attempt_count=run.attempt_count,
            effective_status=summary.effective_status if summary is not None else None,
            total_accounts=summary.total_accounts if summary is not None else None,
            completed_accounts=summary.completed_accounts if summary is not None else None,
            pending_accounts=summary.pending_accounts if summary is not None else None,
            cycle_key=summary.cycle_key if summary is not None else None,
        )

    @staticmethod
    def _is_retryable_account_failure(error_code: str | None) -> bool:
        if not error_code:
            return False
        return error_code.lower() in _RETRYABLE_ACCOUNT_FAILURE_CODES

    @staticmethod
    def _is_account_eligible_for_automation(
        account: Account,
        *,
        include_paused_accounts: bool,
    ) -> bool:
        if account.status in _AUTOMATION_ALWAYS_SKIPPED_ACCOUNT_STATUSES:
            return False
        if account.status == AccountStatus.PAUSED and not include_paused_accounts:
            return False
        return True

    async def _write_request_log(
        self,
        *,
        account_id: str | None,
        request_id: str,
        model: str,
        reasoning_effort: str | None = None,
        latency_ms: int | None,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        service_tier: str | None = None,
    ) -> None:
        if self._request_logs_repository is None:
            return
        try:
            await self._request_logs_repository.add_log(
                account_id=account_id,
                request_id=request_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_effort=reasoning_effort,
                latency_ms=latency_ms,
                status=status,
                error_code=error_code,
                error_message=error_message,
                service_tier=service_tier,
                transport="automation",
            )
        except Exception:
            logger.warning(
                "Failed to persist automation request log account_id=%s request_id=%s",
                account_id,
                request_id,
                exc_info=True,
            )


def _normalize_schedule_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized != AUTOMATION_SCHEDULE_DAILY:
        raise AutomationValidationError(
            f"Unsupported schedule type: {value}",
            code="invalid_schedule_type",
        )
    return normalized


def _normalize_non_empty(value: str, *, field_label: str, code: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AutomationValidationError(f"{field_label} is required", code=code)
    return normalized


def _normalize_prompt(value: str | None) -> str:
    if value is None:
        return DEFAULT_AUTOMATION_PROMPT
    normalized = value.strip()
    if not normalized:
        raise AutomationValidationError("prompt cannot be empty", code="invalid_prompt")
    return normalized


def _normalize_reasoning_effort(value: str | None, *, model_slug: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    allowed = {"minimal", "low", "medium", "high", "xhigh"}
    if normalized not in allowed:
        raise AutomationValidationError(
            f"Unsupported reasoning effort: {value}",
            code="invalid_reasoning_effort",
        )

    registry = get_model_registry()
    model = registry.get_models_with_fallback().get(model_slug)
    if model is None:
        return normalized
    supported = {level.effort.strip().lower() for level in model.supported_reasoning_levels if level.effort.strip()}
    if normalized not in supported:
        raise AutomationValidationError(
            f"Reasoning effort '{normalized}' is not supported by model '{model_slug}'",
            code="invalid_reasoning_effort",
        )
    return normalized


def _build_dispatch_plan(
    *,
    job_id: str,
    due_slot: datetime,
    account_ids: list[str],
    threshold_minutes: int,
) -> list[tuple[str, datetime]]:
    deduped_account_ids = list(dict.fromkeys(account_ids))
    if not deduped_account_ids:
        return []
    offsets = _pick_dispatch_offsets_seconds(
        job_id=job_id,
        due_slot=due_slot,
        account_count=len(deduped_account_ids),
        threshold_minutes=threshold_minutes,
    )
    return [
        (account_id, due_slot + timedelta(seconds=offset_seconds))
        for account_id, offset_seconds in zip(deduped_account_ids, offsets, strict=False)
    ]


def _pick_dispatch_offsets_seconds(
    *,
    job_id: str,
    due_slot: datetime,
    account_count: int,
    threshold_minutes: int,
) -> list[int]:
    if account_count <= 0:
        return []
    if threshold_minutes <= 0:
        return [0] * account_count

    max_offset_seconds = threshold_minutes * 60
    available_non_zero_offsets = list(range(1, max_offset_seconds + 1))
    seed = f"{job_id}:{due_slot.isoformat()}"
    rng = random.Random(seed)

    if account_count == 1:
        return [0]

    requested_non_zero_offsets = account_count - 1
    if requested_non_zero_offsets <= len(available_non_zero_offsets):
        offsets = [0, *rng.sample(available_non_zero_offsets, requested_non_zero_offsets)]
        rng.shuffle(offsets)
        return offsets

    offsets = [0, *available_non_zero_offsets]
    rng.shuffle(offsets)
    for _ in range(requested_non_zero_offsets - len(available_non_zero_offsets)):
        offsets.append(rng.choice(available_non_zero_offsets))
    rng.shuffle(offsets)
    return offsets


def _scheduled_slot_key(
    job_id: str,
    *,
    account_id: str | None = None,
    due_slot: datetime | None = None,
) -> str:
    slot_anchor = due_slot
    if slot_anchor is None:
        raise ValueError("due_slot is required for scheduled slot keys")
    if account_id is None:
        return f"scheduled:{job_id}:{slot_anchor.isoformat()}Z"
    seed = f"{job_id}:{slot_anchor.isoformat()}:{account_id}"
    digest = sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"scheduled:{job_id}:{digest}"


def _scheduled_cycle_key(job_id: str, due_slot: datetime) -> str:
    return f"scheduled:{job_id}:{due_slot.isoformat()}"


def _manual_cycle_key(job_id: str, cycle_id: str) -> str:
    return f"manual:{job_id}:{cycle_id}"


def _normalize_legacy_manual_cycle_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    parts = stripped.split(":")
    if len(parts) == 3 and parts[0] == "manual" and parts[1] and parts[2]:
        return stripped
    if len(parts) == 4 and parts[0] == "manual" and parts[1] and parts[2]:
        return f"manual:{parts[1]}:{parts[2]}"
    return stripped


def _manual_slot_key(job_id: str, cycle_id: str, account_id: str) -> str:
    seed = f"{job_id}:{cycle_id}:{account_id}"
    digest = sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"{_manual_cycle_key(job_id, cycle_id)}:{digest}"


def _parse_manual_cycle_key(slot_key: str) -> tuple[str, str] | None:
    parts = slot_key.split(":")
    if len(parts) != 4:
        return None
    trigger, job_id, cycle_id, _digest = parts
    if trigger != "manual" or not job_id or not cycle_id:
        return None
    return cycle_id, f"manual:{job_id}:{cycle_id}:"


def _resolve_effective_status(
    *,
    pending_accounts: int,
    completed_accounts: int,
    success_count: int,
    failed_count: int,
    partial_count: int,
    running_count: int,
    fallback_status: str,
    now_utc: datetime,
    window_end_utc: datetime,
) -> str:
    if running_count > 0:
        return AUTOMATION_RUN_STATUS_RUNNING
    if pending_accounts > 0 and now_utc <= window_end_utc:
        return AUTOMATION_RUN_STATUS_RUNNING
    if pending_accounts > 0:
        return AUTOMATION_RUN_STATUS_PARTIAL if completed_accounts > 0 else AUTOMATION_RUN_STATUS_FAILED
    if success_count > 0 and failed_count == 0 and partial_count == 0:
        return AUTOMATION_RUN_STATUS_SUCCESS
    if success_count > 0 and (failed_count > 0 or partial_count > 0):
        return AUTOMATION_RUN_STATUS_PARTIAL
    if failed_count > 0 and success_count == 0 and partial_count == 0:
        return AUTOMATION_RUN_STATUS_FAILED
    if partial_count > 0:
        return AUTOMATION_RUN_STATUS_PARTIAL
    return fallback_status


def _resolve_cycle_finished_at(
    account_states: list[AutomationRunAccountStateData],
    *,
    pending_accounts: int,
) -> datetime | None:
    if pending_accounts > 0:
        return None
    return max((entry.finished_at for entry in account_states if entry.finished_at is not None), default=None)


def _resolve_cycle_error_summary(
    account_states: list[AutomationRunAccountStateData],
) -> tuple[str | None, str | None]:
    failed_states = [
        entry
        for entry in account_states
        if entry.status in {AUTOMATION_RUN_STATUS_FAILED, AUTOMATION_RUN_STATUS_PARTIAL}
    ]
    if not failed_states:
        return None, None
    if len(failed_states) == 1:
        failure = failed_states[0]
        if failure.error_code or failure.error_message:
            return failure.error_code, failure.error_message
        return "account_failure", "1 account failed in this cycle"

    shared_codes = {entry.error_code for entry in failed_states if entry.error_code}
    error_code = next(iter(shared_codes)) if len(shared_codes) == 1 else "multiple_account_failures"
    error_message = f"{len(failed_states)} accounts failed in this cycle"
    return error_code, error_message


def _extract_proxy_error(exc: ProxyResponseError) -> tuple[str, str]:
    payload = exc.payload
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code_value = error.get("code")
            message_value = error.get("message")
            code = (
                code_value.strip().lower() if isinstance(code_value, str) and code_value.strip() else "upstream_error"
            )
            if isinstance(message_value, str) and message_value.strip():
                return code, message_value.strip()
            return code, "Upstream request failed"
    return "upstream_error", "Upstream request failed"


def _normalize_job_status_filters(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    normalized = sorted({value.strip().lower() for value in values if value and value.strip()})
    if not normalized or "all" in normalized:
        return None
    return normalized


def _normalize_run_status_filters(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    normalized = sorted({value.strip().lower() for value in values if value and value.strip()})
    if not normalized or "all" in normalized:
        return None
    return [value for value in normalized if value in _all_run_statuses()] or None


def _normalize_run_trigger_filters(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    normalized = sorted({value.strip().lower() for value in values if value and value.strip()})
    if not normalized or "all" in normalized:
        return None
    return [value for value in normalized if value in _all_run_triggers()] or None


def _all_run_statuses() -> list[str]:
    return [
        AUTOMATION_RUN_STATUS_RUNNING,
        AUTOMATION_RUN_STATUS_SUCCESS,
        AUTOMATION_RUN_STATUS_FAILED,
        AUTOMATION_RUN_STATUS_PARTIAL,
    ]


def _all_run_triggers() -> list[str]:
    return [AUTOMATION_RUN_TRIGGER_SCHEDULED, AUTOMATION_RUN_TRIGGER_MANUAL]


def _automation_request_id(response_id: str | None, run_id: str, attempt_count: int) -> str:
    if isinstance(response_id, str):
        normalized = response_id.strip()
        if normalized:
            return normalized
    return f"automation-{run_id}-attempt-{attempt_count}"


def _manual_run_execution_claim_timeout_seconds() -> float:
    settings = get_settings()
    return max(30.0, settings.compact_request_budget_seconds + 30.0)


def _elapsed_ms(started_at: float | None) -> int | None:
    if started_at is None:
        return None
    return max(0, int((time.monotonic() - started_at) * 1000))


def _extract_compact_usage_fields(
    compact_response: object,
) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    usage = getattr(compact_response, "usage", None)
    input_tokens = _coerce_int(getattr(usage, "input_tokens", None))
    output_tokens = _coerce_int(getattr(usage, "output_tokens", None))
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached_input_tokens = _coerce_int(getattr(input_details, "cached_tokens", None))
    reasoning_tokens = _coerce_int(getattr(output_details, "reasoning_tokens", None))

    service_tier: str | None = None
    model_extra = getattr(compact_response, "model_extra", None)
    if isinstance(model_extra, dict):
        service_tier_raw = model_extra.get("service_tier")
        if isinstance(service_tier_raw, str):
            normalized = service_tier_raw.strip()
            if normalized:
                service_tier = normalized

    return input_tokens, output_tokens, cached_input_tokens, reasoning_tokens, service_tier


def _coerce_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None
