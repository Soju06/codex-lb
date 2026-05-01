from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Body, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardBadRequestError, DashboardNotFoundError
from app.dependencies import AutomationsContext, get_automations_context
from app.modules.automations.schemas import (
    AutomationDeleteResponse,
    AutomationJobCreateRequest,
    AutomationJobFilterOptionsResponse,
    AutomationJobResponse,
    AutomationJobsListResponse,
    AutomationJobUpdateRequest,
    AutomationRunAccountStateResponse,
    AutomationRunDetailsResponse,
    AutomationRunFilterOptionsResponse,
    AutomationRunResponse,
    AutomationRunsListResponse,
    AutomationScheduleResponse,
)
from app.modules.automations.service import (
    AutomationJobCreateInput,
    AutomationJobData,
    AutomationJobUpdateInput,
    AutomationNotFoundError,
    AutomationRunAccountStateData,
    AutomationRunData,
    AutomationRunDetailsData,
    AutomationValidationError,
)

router = APIRouter(
    prefix="/api/automations",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=AutomationJobsListResponse)
async def list_automations(
    limit: int = Query(default=25, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    model: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    schedule_type: list[str] | None = Query(default=None, alias="scheduleType"),
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationJobsListResponse:
    page = await context.service.list_jobs_page(
        limit=limit,
        offset=offset,
        search=search,
        account_ids=account_id,
        models=model,
        statuses=status,
        schedule_types=schedule_type,
    )
    return AutomationJobsListResponse(
        items=[_to_job_response(job) for job in page.items],
        total=page.total,
        has_more=page.has_more,
    )


@router.get("/options", response_model=AutomationJobFilterOptionsResponse)
async def list_automation_filter_options(
    search: str | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    model: list[str] | None = Query(default=None),
    schedule_type: list[str] | None = Query(default=None, alias="scheduleType"),
    status: list[str] | None = Query(default=None),
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationJobFilterOptionsResponse:
    options = await context.service.list_job_filter_options(
        search=search,
        account_ids=account_id,
        models=model,
        statuses=status,
        schedule_types=schedule_type,
    )
    return AutomationJobFilterOptionsResponse(
        account_ids=options.account_ids,
        models=options.models,
        statuses=options.statuses,
        schedule_types=options.schedule_types,
    )


@router.get("/runs", response_model=AutomationRunsListResponse)
async def list_automations_runs(
    limit: int = Query(default=25, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    model: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    trigger: list[str] | None = Query(default=None),
    automation_id: list[str] | None = Query(default=None, alias="automationId"),
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationRunsListResponse:
    page = await context.service.list_runs_page(
        limit=limit,
        offset=offset,
        search=search,
        account_ids=account_id,
        models=model,
        statuses=status,
        triggers=trigger,
        job_ids=automation_id,
    )
    return AutomationRunsListResponse(
        items=[_to_run_response(run) for run in page.items],
        total=page.total,
        has_more=page.has_more,
    )


@router.get("/runs/options", response_model=AutomationRunFilterOptionsResponse)
async def list_automation_run_filter_options(
    search: str | None = Query(default=None),
    account_id: list[str] | None = Query(default=None, alias="accountId"),
    model: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    trigger: list[str] | None = Query(default=None),
    automation_id: list[str] | None = Query(default=None, alias="automationId"),
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationRunFilterOptionsResponse:
    options = await context.service.list_run_filter_options(
        search=search,
        account_ids=account_id,
        models=model,
        statuses=status,
        triggers=trigger,
        job_ids=automation_id,
    )
    return AutomationRunFilterOptionsResponse(
        account_ids=options.account_ids,
        models=options.models,
        statuses=options.statuses,
        triggers=options.triggers,
    )


@router.get("/runs/{run_id}/details", response_model=AutomationRunDetailsResponse)
async def get_automation_run_details(
    run_id: str,
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationRunDetailsResponse:
    try:
        details = await context.service.get_run_details(run_id)
    except AutomationNotFoundError as exc:
        raise DashboardNotFoundError("Automation run not found", code="automation_run_not_found") from exc
    return _to_run_details_response(details)


@router.post("", response_model=AutomationJobResponse)
async def create_automation(
    payload: AutomationJobCreateRequest = Body(...),
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationJobResponse:
    try:
        job = await context.service.create_job(
            AutomationJobCreateInput(
                name=payload.name,
                enabled=payload.enabled,
                include_paused_accounts=payload.include_paused_accounts,
                schedule_type=payload.schedule.type,
                schedule_time=payload.schedule.time,
                schedule_timezone=payload.schedule.timezone,
                schedule_threshold_minutes=payload.schedule.threshold_minutes,
                schedule_days=list(payload.schedule.days),
                model=payload.model,
                reasoning_effort=payload.reasoning_effort,
                prompt=payload.prompt,
                account_ids=payload.account_ids,
            )
        )
    except AutomationValidationError as exc:
        raise DashboardBadRequestError(str(exc), code=exc.code) from exc
    return _to_job_response(job)


@router.patch("/{automation_id}", response_model=AutomationJobResponse)
async def update_automation(
    automation_id: str,
    payload: AutomationJobUpdateRequest,
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationJobResponse:
    try:
        updated = await context.service.update_job(
            automation_id,
            AutomationJobUpdateInput(
                name=payload.name,
                enabled=payload.enabled,
                include_paused_accounts=payload.include_paused_accounts,
                schedule_type=payload.schedule.type if payload.schedule else None,
                schedule_time=payload.schedule.time if payload.schedule else None,
                schedule_timezone=payload.schedule.timezone if payload.schedule else None,
                schedule_threshold_minutes=payload.schedule.threshold_minutes if payload.schedule else None,
                schedule_days=list(payload.schedule.days) if payload.schedule else None,
                model=payload.model,
                reasoning_effort=payload.reasoning_effort,
                reasoning_effort_set="reasoning_effort" in payload.model_fields_set,
                prompt=payload.prompt,
                account_ids=payload.account_ids,
            ),
        )
    except AutomationNotFoundError as exc:
        raise DashboardNotFoundError("Automation not found", code="automation_not_found") from exc
    except AutomationValidationError as exc:
        raise DashboardBadRequestError(str(exc), code=exc.code) from exc
    return _to_job_response(updated)


@router.delete("/{automation_id}", response_model=AutomationDeleteResponse)
async def delete_automation(
    automation_id: str,
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationDeleteResponse:
    deleted = await context.service.delete_job(automation_id)
    if not deleted:
        raise DashboardNotFoundError("Automation not found", code="automation_not_found")
    return AutomationDeleteResponse(status="deleted")


@router.post("/{automation_id}/run-now", response_model=AutomationRunResponse, status_code=202)
async def run_automation_now(
    automation_id: str,
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationRunResponse:
    try:
        run = await context.service.run_now(automation_id)
    except AutomationNotFoundError as exc:
        raise DashboardNotFoundError("Automation not found", code="automation_not_found") from exc
    return _to_run_response(run)


@router.get("/{automation_id}/runs", response_model=AutomationRunsListResponse)
async def list_automation_runs(
    automation_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    context: AutomationsContext = Depends(get_automations_context),
) -> AutomationRunsListResponse:
    try:
        runs = await context.service.list_runs(automation_id, limit=limit)
    except AutomationNotFoundError as exc:
        raise DashboardNotFoundError("Automation not found", code="automation_not_found") from exc
    return AutomationRunsListResponse(items=[_to_run_response(run) for run in runs], total=len(runs), has_more=False)


def _to_job_response(job: AutomationJobData) -> AutomationJobResponse:
    return AutomationJobResponse(
        id=job.id,
        name=job.name,
        enabled=job.enabled,
        include_paused_accounts=job.include_paused_accounts,
        schedule=AutomationScheduleResponse(
            type=_schedule_type_literal(job.schedule.type),
            time=job.schedule.time,
            timezone=job.schedule.timezone,
            threshold_minutes=job.schedule.threshold_minutes,
            days=_schedule_days_literals(job.schedule.days),
        ),
        model=job.model,
        reasoning_effort=job.reasoning_effort,
        prompt=job.prompt,
        account_ids=job.account_ids,
        next_run_at=job.next_run_at,
        last_run=_to_run_response(job.last_run) if job.last_run else None,
    )


def _to_run_response(run: AutomationRunData) -> AutomationRunResponse:
    return AutomationRunResponse(
        id=run.id,
        job_id=run.job_id,
        job_name=run.job_name,
        model=run.model,
        reasoning_effort=run.reasoning_effort,
        trigger=_run_trigger_literal(run.trigger),
        status=_run_status_literal(run.status),
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        finished_at=run.finished_at,
        account_id=run.account_id,
        error_code=run.error_code,
        error_message=run.error_message,
        attempt_count=run.attempt_count,
        effective_status=_run_status_literal(run.effective_status) if run.effective_status else None,
        total_accounts=run.total_accounts,
        completed_accounts=run.completed_accounts,
        pending_accounts=run.pending_accounts,
        cycle_key=run.cycle_key,
    )


def _to_run_details_response(details: AutomationRunDetailsData) -> AutomationRunDetailsResponse:
    return AutomationRunDetailsResponse(
        run=_to_run_response(details.run),
        accounts=[_to_run_account_state_response(entry) for entry in details.accounts],
        total_accounts=details.total_accounts,
        completed_accounts=details.completed_accounts,
        pending_accounts=details.pending_accounts,
    )


def _to_run_account_state_response(entry: AutomationRunAccountStateData) -> AutomationRunAccountStateResponse:
    return AutomationRunAccountStateResponse(
        account_id=entry.account_id,
        status=_run_account_state_literal(entry.status),
        run_id=entry.run_id,
        scheduled_for=entry.scheduled_for,
        started_at=entry.started_at,
        finished_at=entry.finished_at,
        error_code=entry.error_code,
        error_message=entry.error_message,
    )


def _schedule_type_literal(value: str) -> Literal["daily"]:
    if value == "daily":
        return "daily"
    raise RuntimeError(f"Unexpected automation schedule type: {value}")


def _run_trigger_literal(value: str) -> Literal["scheduled", "manual"]:
    if value in {"scheduled", "manual"}:
        return cast(Literal["scheduled", "manual"], value)
    raise RuntimeError(f"Unexpected automation run trigger: {value}")


def _run_status_literal(value: str) -> Literal["running", "success", "failed", "partial"]:
    if value in {"running", "success", "failed", "partial"}:
        return cast(Literal["running", "success", "failed", "partial"], value)
    raise RuntimeError(f"Unexpected automation run status: {value}")


def _run_account_state_literal(value: str) -> Literal["pending", "running", "success", "failed", "partial"]:
    if value in {"pending", "running", "success", "failed", "partial"}:
        return cast(Literal["pending", "running", "success", "failed", "partial"], value)
    raise RuntimeError(f"Unexpected automation run account state: {value}")


def _schedule_days_literals(value: list[str]) -> list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]]:
    allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    invalid = [day for day in value if day not in allowed]
    if invalid:
        raise RuntimeError(f"Unexpected automation schedule day values: {invalid!r}")
    return [cast(Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"], day) for day in value]
