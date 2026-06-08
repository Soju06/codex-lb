from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Body, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardBadRequestError, DashboardNotFoundError
from app.db.models import AgentProviderAccount, AgentProviderQuotaWindow, AgentProviderRoutingSettings
from app.dependencies import AgentProviderRoutingContext, get_agent_provider_routing_context
from app.modules.agent_provider_routing.schemas import (
    AgentProviderPreflightAccountState,
    AgentProviderPreflightResponse,
    AgentProviderQuotaWindowResponse,
    AgentProviderQuotaWindowUpsertRequest,
    AgentProviderRoutingSettingsResponse,
    AgentProviderRoutingSettingsUpdateRequest,
    ProviderRoutingStrategy,
)
from app.modules.agent_provider_routing.service import (
    AgentProviderQuotaWindowUpsertData,
    AgentProviderRoutingNotFoundError,
    AgentProviderRoutingSettingsUpdateData,
    AgentProviderRoutingValidationError,
)
from app.modules.agent_providers.schemas import AgentProviderId

router = APIRouter(
    prefix="/api/agent-providers",
    tags=["agent-provider-routing"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _settings_response(row: AgentProviderRoutingSettings) -> AgentProviderRoutingSettingsResponse:
    return AgentProviderRoutingSettingsResponse(
        provider_id=cast(AgentProviderId, row.provider_id),
        strategy=cast(ProviderRoutingStrategy, row.strategy),
        single_account_id=row.single_account_id,
        ordered_account_ids=_parse_account_id_order(row.ordered_account_ids_json),
        quota_threshold_pct=row.quota_threshold_pct,
        round_robin_cursor=row.round_robin_cursor,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _quota_window_response(row: AgentProviderQuotaWindow) -> AgentProviderQuotaWindowResponse:
    return AgentProviderQuotaWindowResponse(
        dimension=row.dimension,
        used=row.used,
        limit=row.limit,
        reset_at=row.reset_at,
        recorded_at=row.recorded_at,
    )


def _account_state_response(row: AgentProviderAccount) -> AgentProviderPreflightAccountState:
    return AgentProviderPreflightAccountState(
        account_id=row.id,
        display_name=row.display_name,
        status=row.status,
        quota_windows=[_quota_window_response(window) for window in row.quota_windows],
    )


@router.get("/{provider_id}/routing/settings", response_model=AgentProviderRoutingSettingsResponse)
async def get_provider_routing_settings(
    provider_id: str,
    context: AgentProviderRoutingContext = Depends(get_agent_provider_routing_context),
) -> AgentProviderRoutingSettingsResponse:
    try:
        row = await context.service.get_settings(provider_id)
    except AgentProviderRoutingValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="unsupported_agent_provider") from exc
    return _settings_response(row)


@router.patch("/{provider_id}/routing/settings", response_model=AgentProviderRoutingSettingsResponse)
async def update_provider_routing_settings(
    provider_id: str,
    payload: AgentProviderRoutingSettingsUpdateRequest = Body(...),
    context: AgentProviderRoutingContext = Depends(get_agent_provider_routing_context),
) -> AgentProviderRoutingSettingsResponse:
    try:
        row = await context.service.update_settings(
            provider_id,
            AgentProviderRoutingSettingsUpdateData(
                strategy=payload.strategy,
                single_account_id=payload.single_account_id,
                single_account_id_set="single_account_id" in payload.model_fields_set,
                ordered_account_ids=None if payload.ordered_account_ids is None else tuple(payload.ordered_account_ids),
                quota_threshold_pct=payload.quota_threshold_pct,
                round_robin_cursor=payload.round_robin_cursor,
            ),
        )
    except AgentProviderRoutingValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_agent_provider_routing") from exc
    return _settings_response(row)


@router.put(
    "/{provider_id}/accounts/{account_id}/quota-windows/{dimension}",
    response_model=AgentProviderQuotaWindowResponse,
)
async def upsert_provider_quota_window(
    provider_id: str,
    account_id: str,
    dimension: str,
    payload: AgentProviderQuotaWindowUpsertRequest = Body(...),
    context: AgentProviderRoutingContext = Depends(get_agent_provider_routing_context),
) -> AgentProviderQuotaWindowResponse:
    if payload.dimension != dimension:
        raise DashboardBadRequestError("dimension path and body must match", code="invalid_agent_provider_quota")
    try:
        row = await context.service.upsert_quota_window(
            provider_id,
            account_id,
            AgentProviderQuotaWindowUpsertData(
                dimension=payload.dimension,
                used=payload.used,
                limit=payload.limit,
                reset_at=payload.reset_at,
            ),
        )
    except AgentProviderRoutingNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="agent_provider_account_not_found") from exc
    except AgentProviderRoutingValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_agent_provider_quota") from exc
    return _quota_window_response(row)


@router.post("/{provider_id}/routing/preflight", response_model=AgentProviderPreflightResponse)
async def preflight_provider_routing(
    provider_id: str,
    context: AgentProviderRoutingContext = Depends(get_agent_provider_routing_context),
) -> AgentProviderPreflightResponse:
    try:
        preflight = await context.service.preflight(provider_id)
    except AgentProviderRoutingValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="unsupported_agent_provider") from exc
    return AgentProviderPreflightResponse(
        provider_id=cast(AgentProviderId, preflight.provider_id),
        selected_account_id=preflight.selected_account_id,
        denied_reason=preflight.denied_reason,
        candidate_account_ids=list(preflight.candidate_account_ids),
        settings=_settings_response(preflight.settings),
        accounts=[_account_state_response(account) for account in preflight.accounts],
    )


def _parse_account_id_order(raw: str | None) -> list[str]:
    if not raw:
        return []
    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_account_id in parsed:
        if not isinstance(raw_account_id, str):
            continue
        account_id = raw_account_id.strip()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        ordered.append(account_id)
    return ordered
