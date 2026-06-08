from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AgentProviderAccount
from app.modules.agent_providers.schemas import (
    AgentProviderCapability,
    AgentProviderId,
    AgentProviderListResponse,
    AgentProviderOverviewItem,
    AgentProviderOverviewResponse,
    AgentProviderOverviewTotals,
    AgentProviderStatus,
    AgentProviderSummary,
    ProviderOverviewTimeframe,
)
from app.modules.request_logs.repository import ProviderRequestLogAggregate


class CodexAccountsRepositoryPort(Protocol):
    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]: ...


class AgentProviderAccountsRepositoryPort(Protocol):
    async def list_by_provider(self, provider_id: str) -> list[AgentProviderAccount]: ...


class AgentProviderRoutingRepositoryPort(Protocol):
    async def list_accounts_with_quota_windows(self, provider_id: str) -> list[AgentProviderAccount]: ...


class RequestLogsRepositoryPort(Protocol):
    async def aggregate_by_provider_since(self, since: datetime) -> list[ProviderRequestLogAggregate]: ...


def list_agent_providers() -> AgentProviderListResponse:
    return AgentProviderListResponse(
        providers=[
            AgentProviderSummary(
                provider_id="codex",
                display_name="Codex",
                status="ready",
                auth_modes=["chatgpt_oauth"],
                quota_dimensions=["primary", "secondary", "additional_quota"],
                dashboard_sections=["accounts", "settings", "usage", "reports", "request_logs"],
                capabilities=[
                    AgentProviderCapability(
                        protocol="codex_chatgpt",
                        status="ready",
                        proxyable=True,
                        streaming=True,
                        lifecycle_notes="Existing production surface remains the primary Codex runtime.",
                        operator_action="Keep using current Codex account and routing settings.",
                        notes="Existing Codex/ChatGPT account pool and proxy routes.",
                    )
                ],
            ),
            AgentProviderSummary(
                provider_id="gemini",
                display_name="Gemini",
                status="foundation",
                auth_modes=["api_key", "google_cloud_adc", "cli_keyring"],
                quota_dimensions=["rpm", "tpm", "rpd", "model_family"],
                dashboard_sections=["accounts", "settings", "usage", "reports", "request_logs"],
                capabilities=[
                    AgentProviderCapability(
                        protocol="gemini_api",
                        status="foundation",
                        proxyable=True,
                        streaming=True,
                        lifecycle_notes="Uses Gemini Developer API HTTP endpoints with native streaming support.",
                        operator_action="Add Gemini API-key accounts, then configure provider-scoped quota windows.",
                        notes="Gemini Developer API is the first proxyable Gemini surface.",
                    ),
                    AgentProviderCapability(
                        protocol="vertex_ai",
                        status="planned",
                        proxyable=True,
                        streaming=True,
                        lifecycle_notes="Requires Google Cloud project/location credential handling before use.",
                        operator_action="Defer until Vertex project and ADC/service-account settings are modeled.",
                        notes="Vertex AI Gemini support needs separate project/location credentials.",
                    ),
                    AgentProviderCapability(
                        protocol="antigravity_cli",
                        status="planned",
                        proxyable=False,
                        streaming=False,
                        lifecycle_notes=(
                            "Antigravity CLI is the migration target for individual Gemini CLI users after 2026-06-18."
                        ),
                        operator_action="Build as an agy harness/session connector, not as a raw HTTP proxy route.",
                        available_until="2026-06-18",
                        notes="Antigravity CLI is modeled as a harness connector, not a raw HTTP proxy target.",
                    ),
                ],
            ),
            AgentProviderSummary(
                provider_id="antigravity",
                display_name="Antigravity",
                status="foundation",
                auth_modes=["api_key", "cli_keyring"],
                quota_dimensions=["requests", "prompt_tokens", "completion_tokens", "sessions", "wall_time"],
                dashboard_sections=["accounts", "settings", "usage", "request_logs"],
                capabilities=[
                    AgentProviderCapability(
                        protocol="interactions_api",
                        status="foundation",
                        proxyable=True,
                        streaming=False,
                        lifecycle_notes=(
                            "Antigravity Agent runs through the Gemini Interactions API as antigravity-preview-05-2026."
                        ),
                        operator_action=(
                            "Add Antigravity API-key accounts, then route antigravity-preview models through "
                            "/v1/chat/completions or /v1/antigravity/interactions."
                        ),
                        available_until=None,
                        notes="Managed Google-hosted agent surface using Api-Revision 2026-05-20.",
                    ),
                    AgentProviderCapability(
                        protocol="antigravity_cli",
                        status="foundation",
                        proxyable=False,
                        streaming=False,
                        lifecycle_notes=(
                            "Antigravity CLI uses local agy harness sessions, system keyring auth, shared settings, "
                            "and workspace state."
                        ),
                        operator_action=(
                            "Register CLI profiles and run dashboard-authenticated agy --print harness probes."
                        ),
                        available_until=None,
                        notes=(
                            "Antigravity is tracked as a CLI harness provider rather than a Gemini HTTP alias; "
                            "settings live under ~/.gemini/antigravity-cli/."
                        ),
                    ),
                ],
            ),
        ]
    )


class AgentProviderOverviewService:
    def __init__(
        self,
        *,
        codex_accounts: CodexAccountsRepositoryPort,
        provider_accounts: AgentProviderAccountsRepositoryPort,
        provider_routing: AgentProviderRoutingRepositoryPort,
        request_logs: RequestLogsRepositoryPort,
    ) -> None:
        self._codex_accounts = codex_accounts
        self._provider_accounts = provider_accounts
        self._provider_routing = provider_routing
        self._request_logs = request_logs

    async def get_overview(self, timeframe: ProviderOverviewTimeframe) -> AgentProviderOverviewResponse:
        providers = list_agent_providers().providers
        since = utcnow() - _timeframe_delta(timeframe)
        codex_accounts = await self._codex_accounts.list_accounts()
        gemini_accounts = await self._provider_accounts.list_by_provider("gemini")
        antigravity_accounts = await self._provider_accounts.list_by_provider("antigravity")
        gemini_quota_accounts = await self._provider_routing.list_accounts_with_quota_windows("gemini")
        antigravity_quota_accounts = await self._provider_routing.list_accounts_with_quota_windows("antigravity")
        request_aggregates = {
            aggregate.provider_id: aggregate
            for aggregate in await self._request_logs.aggregate_by_provider_since(since)
        }

        account_counts: dict[str, tuple[int, int, int]] = {
            "codex": (
                len(codex_accounts),
                sum(1 for account in codex_accounts if account.status == AccountStatus.ACTIVE),
                0,
            ),
            "gemini": _provider_account_counts(gemini_accounts, gemini_quota_accounts),
            "antigravity": _provider_account_counts(antigravity_accounts, antigravity_quota_accounts),
        }
        items = [
            _overview_item(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                status=provider.status,
                account_counts=account_counts[provider.provider_id],
                request_aggregate=request_aggregates.get(provider.provider_id),
            )
            for provider in providers
        ]
        return AgentProviderOverviewResponse(
            timeframe=timeframe,
            providers=items,
            totals=_overview_totals(items),
        )


def _timeframe_delta(timeframe: ProviderOverviewTimeframe) -> timedelta:
    if timeframe == "1d":
        return timedelta(days=1)
    if timeframe == "30d":
        return timedelta(days=30)
    return timedelta(days=7)


def _provider_account_counts(
    accounts: Sequence[AgentProviderAccount],
    quota_accounts: Sequence[AgentProviderAccount],
) -> tuple[int, int, int]:
    return (
        len(accounts),
        sum(1 for account in accounts if account.status == "active"),
        sum(len(account.quota_windows) for account in quota_accounts),
    )


def _overview_item(
    *,
    provider_id: AgentProviderId,
    display_name: str,
    status: AgentProviderStatus,
    account_counts: tuple[int, int, int],
    request_aggregate: ProviderRequestLogAggregate | None,
) -> AgentProviderOverviewItem:
    account_count, active_account_count, quota_window_count = account_counts
    return AgentProviderOverviewItem(
        provider_id=provider_id,
        display_name=display_name,
        status=status,
        account_count=account_count,
        active_account_count=active_account_count,
        quota_window_count=quota_window_count,
        request_count=0 if request_aggregate is None else request_aggregate.request_count,
        success_count=0 if request_aggregate is None else request_aggregate.success_count,
        error_count=0 if request_aggregate is None else request_aggregate.error_count,
        input_tokens=0 if request_aggregate is None else request_aggregate.input_tokens,
        output_tokens=0 if request_aggregate is None else request_aggregate.output_tokens,
        cached_input_tokens=0 if request_aggregate is None else request_aggregate.cached_input_tokens,
    )


def _overview_totals(items: Sequence[AgentProviderOverviewItem]) -> AgentProviderOverviewTotals:
    return AgentProviderOverviewTotals(
        provider_count=len(items),
        account_count=sum(item.account_count for item in items),
        active_account_count=sum(item.active_account_count for item in items),
        quota_window_count=sum(item.quota_window_count for item in items),
        request_count=sum(item.request_count for item in items),
        success_count=sum(item.success_count for item in items),
        error_count=sum(item.error_count for item in items),
        input_tokens=sum(item.input_tokens for item in items),
        output_tokens=sum(item.output_tokens for item in items),
        cached_input_tokens=sum(item.cached_input_tokens for item in items),
    )
