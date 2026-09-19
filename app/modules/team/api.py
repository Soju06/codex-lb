from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.core.audit.service import AuditService
from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardBadRequestError, DashboardNotFoundError
from app.dependencies import TeamContext, get_team_context
from app.modules.api_keys.api import _to_response as _api_key_to_response
from app.modules.api_keys.schemas import ApiKeyCreateResponse
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeyValidationError
from app.modules.team.schemas import (
    TeamMemberCreateRequest,
    TeamMemberKeyCreateRequest,
    TeamMemberKeyResponse,
    TeamMemberResponse,
    TeamMemberUpdateRequest,
    TeamMemberUsageResponse,
    TeamOnboardingResponse,
    TeamOnboardingSnippets,
    TeamUsageDayResponse,
    TeamUsageModelResponse,
    TeamUsageResponse,
    TeamUsageWindowResponse,
)
from app.modules.team.service import (
    TeamMemberCaps,
    TeamMemberCreateData,
    TeamMemberData,
    TeamMemberNotFoundError,
    TeamMemberUpdateData,
    TeamMemberUsageDetail,
    TeamValidationError,
    bump_api_key_cache_namespace,
    invalidate_team_member_caches,
)

router = APIRouter(
    prefix="/api/team",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _to_member_response(member: TeamMemberData) -> TeamMemberResponse:
    return TeamMemberResponse(
        id=member.id,
        name=member.name,
        email=member.email,
        status=member.status,
        cost_cap_day_usd=member.cost_cap_day_usd,
        cost_cap_week_usd=member.cost_cap_week_usd,
        cost_cap_month_usd=member.cost_cap_month_usd,
        token_cap_day=member.token_cap_day,
        token_cap_week=member.token_cap_week,
        token_cap_month=member.token_cap_month,
        allowed_models=member.allowed_models,
        notes=member.notes,
        created_at=member.created_at,
        updated_at=member.updated_at,
        usage=TeamUsageResponse(
            day=_to_window_response(member, "day"),
            week=_to_window_response(member, "week"),
            month=_to_window_response(member, "month"),
        ),
        gate=member.gate,
        keys=[
            TeamMemberKeyResponse(
                id=key.id,
                name=key.name,
                key_prefix=key.key_prefix,
                is_active=key.is_active,
                last_used_at=key.last_used_at,
            )
            for key in member.keys
        ],
    )


def _to_window_response(member: TeamMemberData, window: str) -> TeamUsageWindowResponse:
    totals = member.usage.get(window)
    if totals is None:
        return TeamUsageWindowResponse(cost_usd=0.0, tokens=0)
    return TeamUsageWindowResponse(cost_usd=totals.cost_usd, tokens=totals.tokens)


def _to_usage_response(detail: TeamMemberUsageDetail) -> TeamMemberUsageResponse:
    return TeamMemberUsageResponse(
        member_id=detail.member_id,
        window=detail.window,
        window_start=detail.window_start,
        window_end=detail.window_end,
        totals=TeamUsageWindowResponse(cost_usd=detail.totals.cost_usd, tokens=detail.totals.tokens),
        models=[
            TeamUsageModelResponse(
                model=row.model,
                cost_usd=row.cost_usd,
                tokens=row.tokens,
                requests=row.requests,
            )
            for row in detail.models
        ],
        series=[TeamUsageDayResponse(day=row.day, cost_usd=row.cost_usd, tokens=row.tokens) for row in detail.series],
    )


def _caps_from_payload(payload: TeamMemberCreateRequest) -> TeamMemberCaps:
    return TeamMemberCaps(
        cost_cap_day_usd=payload.cost_cap_day_usd,
        cost_cap_week_usd=payload.cost_cap_week_usd,
        cost_cap_month_usd=payload.cost_cap_month_usd,
        token_cap_day=payload.token_cap_day,
        token_cap_week=payload.token_cap_week,
        token_cap_month=payload.token_cap_month,
    )


@router.get("/members", response_model=list[TeamMemberResponse])
async def list_team_members(
    context: TeamContext = Depends(get_team_context),
) -> list[TeamMemberResponse]:
    members = await context.service.list_members()
    return [_to_member_response(member) for member in members]


@router.post("/members", response_model=TeamMemberResponse)
async def create_team_member(
    request: Request,
    payload: TeamMemberCreateRequest = Body(...),
    context: TeamContext = Depends(get_team_context),
) -> TeamMemberResponse:
    try:
        member = await context.service.create_member(
            TeamMemberCreateData(
                name=payload.name,
                email=payload.email,
                caps=_caps_from_payload(payload),
                allowed_models=payload.allowed_models,
                notes=payload.notes,
                status=payload.status or "active",
            )
        )
    except TeamValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_team_member_payload") from exc
    AuditService.log_async(
        "team_member_created",
        actor_ip=request.client.host if request.client else None,
        details={"member_id": member.id},
    )
    return _to_member_response(member)


@router.patch("/members/{member_id}", response_model=TeamMemberResponse)
async def update_team_member(
    request: Request,
    member_id: str,
    payload: TeamMemberUpdateRequest = Body(...),
    context: TeamContext = Depends(get_team_context),
) -> TeamMemberResponse:
    fields = payload.model_fields_set
    update = TeamMemberUpdateData(
        name=payload.name,
        name_set="name" in fields,
        email=payload.email,
        email_set="email" in fields,
        status=payload.status,
        status_set="status" in fields,
        cost_cap_day_usd=payload.cost_cap_day_usd,
        cost_cap_day_usd_set="cost_cap_day_usd" in fields,
        cost_cap_week_usd=payload.cost_cap_week_usd,
        cost_cap_week_usd_set="cost_cap_week_usd" in fields,
        cost_cap_month_usd=payload.cost_cap_month_usd,
        cost_cap_month_usd_set="cost_cap_month_usd" in fields,
        token_cap_day=payload.token_cap_day,
        token_cap_day_set="token_cap_day" in fields,
        token_cap_week=payload.token_cap_week,
        token_cap_week_set="token_cap_week" in fields,
        token_cap_month=payload.token_cap_month,
        token_cap_month_set="token_cap_month" in fields,
        allowed_models=payload.allowed_models,
        allowed_models_set="allowed_models" in fields,
        notes=payload.notes,
        notes_set="notes" in fields,
    )
    try:
        member = await context.service.update_member(member_id, update)
    except TeamMemberNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc
    except TeamValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_team_member_payload") from exc
    AuditService.log_async(
        "team_member_updated",
        actor_ip=request.client.host if request.client else None,
        details={"member_id": member.id, "changed_fields": sorted(fields)},
    )
    return _to_member_response(member)


@router.delete("/members/{member_id}")
async def delete_team_member(
    request: Request,
    member_id: str,
    context: TeamContext = Depends(get_team_context),
) -> Response:
    try:
        await context.service.delete_member(member_id)
    except TeamMemberNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc
    AuditService.log_async(
        "team_member_deleted",
        actor_ip=request.client.host if request.client else None,
        details={"member_id": member_id},
    )
    return Response(status_code=204)


@router.post("/members/{member_id}/keys", response_model=ApiKeyCreateResponse)
async def create_team_member_key(
    request: Request,
    member_id: str,
    payload: TeamMemberKeyCreateRequest = Body(default=TeamMemberKeyCreateRequest()),
    context: TeamContext = Depends(get_team_context),
) -> ApiKeyCreateResponse:
    try:
        member = await context.service.get_member(member_id)
    except TeamMemberNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc

    try:
        created = await context.api_keys_service.create_key(
            ApiKeyCreateData(
                name=payload.name or member.name,
                allowed_models=None,
                expires_at=payload.expires_at,
                member_id=member.id,
            )
        )
    except ApiKeyValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_api_key_payload") from exc

    invalidate_team_member_caches()
    await bump_api_key_cache_namespace()
    AuditService.log_async(
        "team_member_key_created",
        actor_ip=request.client.host if request.client else None,
        details={"member_id": member.id, "key_id": created.id},
    )
    response = _api_key_to_response(created)
    return ApiKeyCreateResponse(**response.model_dump(), key=created.key)


@router.get("/members/{member_id}/usage", response_model=TeamMemberUsageResponse)
async def get_team_member_usage(
    member_id: str,
    window: str = Query(default="day", pattern=r"^(day|week|month)$"),
    context: TeamContext = Depends(get_team_context),
) -> TeamMemberUsageResponse:
    try:
        detail = await context.service.get_member_usage(member_id, window)
    except TeamMemberNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc
    except TeamValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_team_window") from exc
    return _to_usage_response(detail)


@router.get("/members/{member_id}/onboarding", response_model=TeamOnboardingResponse)
async def get_team_member_onboarding(
    request: Request,
    member_id: str,
    context: TeamContext = Depends(get_team_context),
) -> TeamOnboardingResponse:
    try:
        await context.service.get_member(member_id)
    except TeamMemberNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc

    settings = await get_settings_cache().get()
    configured = (getattr(settings, "team_public_base_url", None) or "").strip()
    base_url = configured.rstrip("/") if configured else str(request.base_url).rstrip("/")
    return TeamOnboardingResponse(base_url=base_url, snippets=_build_snippets(base_url))


def _build_snippets(base_url: str) -> TeamOnboardingSnippets:
    openai_base_url = f"{base_url}/v1"
    windows_powershell = "\n".join(
        (
            "# Claude Code",
            f'$env:ANTHROPIC_BASE_URL = "{base_url}"',
            '$env:ANTHROPIC_AUTH_TOKEN = "<key>"',
            "",
            "# Codex",
            f'$env:OPENAI_BASE_URL = "{openai_base_url}"',
            '$env:OPENAI_API_KEY = "<key>"',
        )
    )
    macos_zsh = "\n".join(
        (
            "# Claude Code",
            f'export ANTHROPIC_BASE_URL="{base_url}"',
            'export ANTHROPIC_AUTH_TOKEN="<key>"',
            "",
            "# Codex",
            f'export OPENAI_BASE_URL="{openai_base_url}"',
            'export OPENAI_API_KEY="<key>"',
        )
    )
    return TeamOnboardingSnippets(windows_powershell=windows_powershell, macos_zsh=macos_zsh)
