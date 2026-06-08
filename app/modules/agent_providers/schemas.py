from __future__ import annotations

from typing import Literal

from app.modules.shared.schemas import DashboardModel

AgentProviderId = Literal["codex", "gemini", "antigravity"]
AgentProviderStatus = Literal["ready", "foundation", "planned"]
AgentProviderProtocol = Literal["codex_chatgpt", "gemini_api", "vertex_ai", "antigravity_cli", "interactions_api"]
AgentProviderAuthMode = Literal["chatgpt_oauth", "api_key", "google_cloud_adc", "cli_keyring"]


class AgentProviderCapability(DashboardModel):
    protocol: AgentProviderProtocol
    status: AgentProviderStatus
    proxyable: bool
    streaming: bool
    lifecycle_notes: str
    operator_action: str
    available_until: str | None = None
    notes: str


class AgentProviderSummary(DashboardModel):
    provider_id: AgentProviderId
    display_name: str
    status: AgentProviderStatus
    auth_modes: list[AgentProviderAuthMode]
    quota_dimensions: list[str]
    dashboard_sections: list[str]
    capabilities: list[AgentProviderCapability]


class AgentProviderListResponse(DashboardModel):
    providers: list[AgentProviderSummary]


ProviderOverviewTimeframe = Literal["1d", "7d", "30d"]


class AgentProviderOverviewItem(DashboardModel):
    provider_id: AgentProviderId
    display_name: str
    status: AgentProviderStatus
    account_count: int
    active_account_count: int
    quota_window_count: int
    request_count: int
    success_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int


class AgentProviderOverviewTotals(DashboardModel):
    provider_count: int
    account_count: int
    active_account_count: int
    quota_window_count: int
    request_count: int
    success_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int


class AgentProviderOverviewResponse(DashboardModel):
    timeframe: ProviderOverviewTimeframe
    providers: list[AgentProviderOverviewItem]
    totals: AgentProviderOverviewTotals
