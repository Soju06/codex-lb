from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.dependencies import AgentProvidersContext, get_agent_providers_context
from app.modules.agent_providers.schemas import (
    AgentProviderListResponse,
    AgentProviderOverviewResponse,
    ProviderOverviewTimeframe,
)
from app.modules.agent_providers.service import list_agent_providers

router = APIRouter(
    prefix="/api/agent-providers",
    tags=["agent-providers"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=AgentProviderListResponse)
async def get_agent_providers() -> AgentProviderListResponse:
    return list_agent_providers()


@router.get("/overview", response_model=AgentProviderOverviewResponse)
async def get_agent_provider_overview(
    timeframe: ProviderOverviewTimeframe = "7d",
    context: AgentProvidersContext = Depends(get_agent_providers_context),
) -> AgentProviderOverviewResponse:
    return await context.service.get_overview(timeframe)
