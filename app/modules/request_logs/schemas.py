from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class RequestLogCostBreakdown(DashboardModel):
    input_usd: float | None = None
    cached_input_usd: float | None = None
    output_usd: float | None = None
    total_usd: float | None = None


class RequestLogEntry(DashboardModel):
    requested_at: datetime
    conversation_id: str | None = None
    account_id: str | None = None
    plan_type: str | None = None
    api_key_id: str | None = None
    api_key_name: str | None = None
    request_id: str
    archive_request_id: str | None = None
    request_kind: str = "normal"
    model: str
    source: str | None = None
    model_source_id: str | None = None
    model_source_kind: str | None = None
    useragent: str | None = None
    useragent_group: str | None = None
    client_ip: str | None = None
    transport: str | None = None
    upstream_transport: str | None = None
    service_tier: str | None = None
    requested_service_tier: str | None = None
    actual_service_tier: str | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    failure_phase: str | None = None
    failure_detail: str | None = None
    failure_exception_type: str | None = None
    upstream_status_code: int | None = None
    upstream_error_code: str | None = None
    bridge_stage: str | None = None
    tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    output_tokens_raw: int | None = None
    reasoning_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_effort: str | None = None
    cost_usd: float | None = None
    cost_breakdown: RequestLogCostBreakdown = Field(default_factory=RequestLogCostBreakdown)
    latency_ms: int | None = None
    latency_first_token_ms: int | None = None
    latency_queue_ms: int | None = None


class RequestLogConversation(DashboardModel):
    request_count: int
    aggregated_cost_usd: float


class RequestLogsResponse(DashboardModel):
    requests: list[RequestLogEntry] = Field(default_factory=list)
    total: int
    has_more: bool
    conversation: RequestLogConversation | None = None


class RequestLogModelOption(DashboardModel):
    model: str
    reasoning_effort: str | None = None


class RequestLogApiKeyOption(DashboardModel):
    id: str
    name: str
    key_prefix: str | None = None


class RequestLogFilterOptionsResponse(DashboardModel):
    account_ids: list[str] = Field(default_factory=list)
    model_options: list[RequestLogModelOption] = Field(default_factory=list)
    api_keys: list[RequestLogApiKeyOption] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)


class ConversationModelEffort(DashboardModel):
    model: str
    reasoning_effort: str | None = None


class ConversationModelStat(DashboardModel):
    model_effort: ConversationModelEffort
    reqs: int
    total_elapsed_time: int
    total_input_tokens: int
    cached_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float


class ConversationEntry(DashboardModel):
    conversation_id: str
    last_request: datetime
    representative_account: str | None = None
    remaining_account_count: int
    api_key_id: str | None = None
    api_key_name: str | None = None
    representative_model: str | None = None
    remaining_model_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


class ConversationsResponse(DashboardModel):
    conversations: list[ConversationEntry] = Field(default_factory=list)
    total: int
    has_more: bool


class ConversationDetailsResponse(DashboardModel):
    conversation_id: str
    start: datetime
    latest: datetime
    account_count: int
    total_elapsed_time: int
    dominant_useragent_group: str | None = None
    model_stats: list[ConversationModelStat] = Field(default_factory=list)
