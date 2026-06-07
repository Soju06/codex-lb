from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, Query, Request

from app.core.audit.service import AuditService
from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardBadRequestError
from app.dependencies import QuotaPlannerContext, get_quota_planner_context
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.load_balancer import _build_states
from app.modules.quota_planner.logic import PlannerSettings, build_demand_forecast, simulate_pool
from app.modules.quota_planner.schemas import (
    QuotaPlannerDecisionResponse,
    QuotaPlannerForecastResponse,
    QuotaPlannerForecastSlotResponse,
    QuotaPlannerSettingsResponse,
    QuotaPlannerSettingsUpdateRequest,
    QuotaPlannerSimulationResponse,
    QuotaPlannerWarmNowRequest,
    QuotaPlannerWarmupActionResponse,
)
from app.modules.usage.repository import UsageRepository

from .warmup import QuotaWarmupService

router = APIRouter(
    prefix="/api/quota-planner",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _settings_response(settings: PlannerSettings) -> QuotaPlannerSettingsResponse:
    return QuotaPlannerSettingsResponse(
        mode=settings.mode,
        timezone=settings.timezone,
        working_days=list(settings.working_days),
        working_hours_start=settings.working_hours_start,
        working_hours_end=settings.working_hours_end,
        prewarm_enabled=settings.prewarm_enabled,
        prewarm_lead_minutes=settings.prewarm_lead_minutes,
        max_warmups_per_day=settings.max_warmups_per_day,
        max_warmup_credits_per_day=settings.max_warmup_credits_per_day,
        min_expected_gain=settings.min_expected_gain,
        forecast_quantile=settings.forecast_quantile,
        allow_synthetic_traffic=settings.allow_synthetic_traffic,
        warmup_model_preference=settings.warmup_model_preference,
        dry_run=settings.dry_run,
    )


def _decision_response(row) -> QuotaPlannerDecisionResponse:
    details = None
    if row.state_before_json:
        try:
            decoded = json.loads(row.state_before_json)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            details = decoded
    return QuotaPlannerDecisionResponse(
        id=row.id,
        created_at=row.created_at,
        mode=row.mode,
        account_id=row.account_id,
        action=row.action,
        scheduled_at=row.scheduled_at,
        executed_at=row.executed_at,
        score=row.score,
        reason=row.reason,
        details=details,
        status=row.status,
        idempotency_key=row.idempotency_key,
    )


def _forecast_response(forecast, simulation) -> QuotaPlannerForecastResponse:
    return QuotaPlannerForecastResponse(
        generated_at=forecast.generated_at,
        horizon_hours=forecast.horizon_hours,
        slot_seconds=forecast.slot_seconds,
        total_demand_units=forecast.total_demand_units,
        peak_slot_start=forecast.peak_slot_start,
        peak_demand_units=forecast.peak_demand_units,
        simulation=QuotaPlannerSimulationResponse(
            loss=simulation.loss,
            unmet_demand=simulation.unmet_demand,
            wasted_capacity=simulation.wasted_capacity,
            cold_start_penalty=simulation.cold_start_penalty,
            synchronization_penalty=simulation.synchronization_penalty,
            forecast_units=simulation.forecast_units,
            served_units=simulation.served_units,
        ),
        slots=[
            QuotaPlannerForecastSlotResponse(
                slot_start=slot.slot_start,
                demand_units=slot.demand_units,
                request_count=slot.request_count,
                source=slot.source,
            )
            for slot in forecast.slots
        ],
    )


def _warmup_action_response(result) -> QuotaPlannerWarmupActionResponse:
    return QuotaPlannerWarmupActionResponse(
        decision_id=result.decision_id,
        status=result.status,
        reason=result.reason,
        request_id=result.request_id,
        executed_at=result.executed_at,
    )


def _validate_working_days(days: list[int] | None, current: tuple[int, ...]) -> tuple[int, ...]:
    if days is None:
        return current
    normalized = tuple(sorted({int(day) for day in days if 0 <= int(day) <= 6}))
    if not normalized:
        raise DashboardBadRequestError("workingDays must include at least one weekday", code="invalid_quota_planner")
    if len(normalized) != len(days):
        raise DashboardBadRequestError(
            "workingDays must contain unique weekday numbers 0-6",
            code="invalid_quota_planner",
        )
    return normalized


@router.get("/settings", response_model=QuotaPlannerSettingsResponse)
async def get_quota_planner_settings(
    context: QuotaPlannerContext = Depends(get_quota_planner_context),
) -> QuotaPlannerSettingsResponse:
    return _settings_response(await context.repository.get_settings())


@router.put("/settings", response_model=QuotaPlannerSettingsResponse)
async def update_quota_planner_settings(
    request: Request,
    payload: QuotaPlannerSettingsUpdateRequest = Body(...),
    context: QuotaPlannerContext = Depends(get_quota_planner_context),
) -> QuotaPlannerSettingsResponse:
    current = await context.repository.get_settings()
    updated = PlannerSettings(
        mode=payload.mode or current.mode,
        timezone=(payload.timezone or current.timezone).strip() or current.timezone,
        working_days=_validate_working_days(payload.working_days, current.working_days),
        working_hours_start=payload.working_hours_start or current.working_hours_start,
        working_hours_end=payload.working_hours_end or current.working_hours_end,
        prewarm_enabled=payload.prewarm_enabled if payload.prewarm_enabled is not None else current.prewarm_enabled,
        prewarm_lead_minutes=(
            payload.prewarm_lead_minutes if payload.prewarm_lead_minutes is not None else current.prewarm_lead_minutes
        ),
        max_warmups_per_day=(
            payload.max_warmups_per_day if payload.max_warmups_per_day is not None else current.max_warmups_per_day
        ),
        max_warmup_credits_per_day=(
            payload.max_warmup_credits_per_day
            if payload.max_warmup_credits_per_day is not None
            else current.max_warmup_credits_per_day
        ),
        min_expected_gain=(
            payload.min_expected_gain if payload.min_expected_gain is not None else current.min_expected_gain
        ),
        forecast_quantile=payload.forecast_quantile or current.forecast_quantile,
        allow_synthetic_traffic=(
            payload.allow_synthetic_traffic
            if payload.allow_synthetic_traffic is not None
            else current.allow_synthetic_traffic
        ),
        warmup_model_preference=(
            payload.warmup_model_preference
            if "warmup_model_preference" in payload.model_fields_set
            else current.warmup_model_preference
        ),
        dry_run=payload.dry_run if payload.dry_run is not None else current.dry_run,
    )
    saved = await context.repository.upsert_settings(updated)
    get_account_selection_cache().invalidate()
    AuditService.log_async(
        "quota_planner_settings_changed",
        actor_ip=request.client.host if request.client else None,
        details={"mode": saved.mode},
    )
    return _settings_response(saved)


@router.get("/decisions", response_model=list[QuotaPlannerDecisionResponse])
async def get_quota_planner_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    context: QuotaPlannerContext = Depends(get_quota_planner_context),
) -> list[QuotaPlannerDecisionResponse]:
    return [_decision_response(row) for row in await context.repository.recent_decisions(limit=limit)]


@router.get("/forecast", response_model=QuotaPlannerForecastResponse)
async def get_quota_planner_forecast(
    horizon_hours: int = Query(default=36, ge=1, le=168, alias="horizonHours"),
    context: QuotaPlannerContext = Depends(get_quota_planner_context),
) -> QuotaPlannerForecastResponse:
    settings = await context.repository.get_settings()
    demand_bins = await context.repository.aggregate_demand_bins()
    forecast = build_demand_forecast(settings=settings, bins=demand_bins, horizon_hours=horizon_hours)
    accounts = await AccountsRepository(context.session).list_accounts()
    usage_repo = UsageRepository(context.session)
    latest_primary = await usage_repo.latest_by_account()
    latest_secondary = await usage_repo.latest_by_account(window="secondary")
    latest_monthly = await usage_repo.latest_by_account(window="monthly")
    states, _ = _build_states(
        accounts=accounts,
        latest_primary=latest_primary,
        latest_secondary=latest_secondary,
        latest_monthly=latest_monthly,
        runtime={},
    )
    simulation = simulate_pool(settings=settings, states=states, demand_forecast=forecast)
    return _forecast_response(forecast, simulation)


@router.post("/warm-now", response_model=QuotaPlannerWarmupActionResponse)
async def quota_planner_warm_now(
    payload: QuotaPlannerWarmNowRequest = Body(...),
    context: QuotaPlannerContext = Depends(get_quota_planner_context),
) -> QuotaPlannerWarmupActionResponse:
    result = await QuotaWarmupService(context.session).warm_now(
        account_id=payload.account_id,
        model=payload.model,
        api_key_id=payload.api_key_id,
        force_probe=payload.force_probe,
    )
    return _warmup_action_response(result)


@router.post("/decisions/{decision_id}/cancel", response_model=QuotaPlannerWarmupActionResponse)
async def quota_planner_cancel_decision(
    decision_id: str,
    context: QuotaPlannerContext = Depends(get_quota_planner_context),
) -> QuotaPlannerWarmupActionResponse:
    result = await QuotaWarmupService(context.session).cancel_decision(decision_id)
    if result is None:
        raise DashboardBadRequestError("Decision not found", code="quota_planner_decision_not_found")
    return _warmup_action_response(result)
