from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

from fastapi import Depends, FastAPI, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_background_session, get_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.service import AccountsService
from app.modules.agent_provider_accounts.repository import AgentProviderAccountsRepository
from app.modules.agent_provider_accounts.service import AgentProviderAccountsService
from app.modules.agent_provider_routing.repository import AgentProviderRoutingRepository
from app.modules.agent_provider_routing.service import AgentProviderRoutingService
from app.modules.agent_provider_runtime.service import (
    AntigravityHarnessService,
    AntigravityManagedAgentService,
    ApiKeyUsagePort,
    GeminiRuntimeService,
    RequestLogWriterPort,
)
from app.modules.agent_providers.service import AgentProviderOverviewService
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditLogsService
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import DashboardService
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_auth.service import (
    DashboardAuthRepositoryProtocol,
    DashboardAuthService,
    get_dashboard_session_store,
)
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallRepositoryPort, FirewallService
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.oauth.service import OauthService
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.service import ProxyService
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.service import ReportsService
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.request_logs.service import RequestLogsService
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.service import SettingsService
from app.modules.sticky_sessions.service import StickySessionsService
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.service import UsageService


@dataclass(slots=True)
class AccountsContext:
    session: AsyncSession
    repository: AccountsRepository
    service: AccountsService


@dataclass(slots=True)
class AgentProviderAccountsContext:
    session: AsyncSession
    repository: AgentProviderAccountsRepository
    service: AgentProviderAccountsService


@dataclass(slots=True)
class AgentProviderRoutingContext:
    session: AsyncSession
    repository: AgentProviderRoutingRepository
    service: AgentProviderRoutingService


@dataclass(slots=True)
class AgentProviderRuntimeContext:
    session: AsyncSession
    routing_repository: AgentProviderRoutingRepository
    routing_service: AgentProviderRoutingService
    api_keys_repository: ApiKeysRepository
    request_logs_repository: RequestLogsRepository
    gemini_service: GeminiRuntimeService
    antigravity_service: AntigravityHarnessService
    antigravity_managed_service: AntigravityManagedAgentService


@dataclass(slots=True)
class AgentProvidersContext:
    session: AsyncSession
    service: AgentProviderOverviewService


@dataclass(slots=True)
class AuditContext:
    session: AsyncSession
    repository: AuditRepository
    service: AuditLogsService


@dataclass(slots=True)
class UsageContext:
    session: AsyncSession
    usage_repository: UsageRepository
    service: UsageService


@dataclass(slots=True)
class OauthContext:
    service: OauthService


@dataclass(slots=True)
class DashboardAuthContext:
    session: AsyncSession
    repository: DashboardAuthRepository
    service: DashboardAuthService


@dataclass(slots=True)
class ProxyContext:
    service: ProxyService


@dataclass(slots=True)
class ApiKeysContext:
    session: AsyncSession
    repository: ApiKeysRepository
    service: ApiKeysService


@dataclass(slots=True)
class RequestLogsContext:
    session: AsyncSession
    repository: RequestLogsRepository
    service: RequestLogsService


@dataclass(slots=True)
class QuotaPlannerContext:
    session: AsyncSession
    repository: QuotaPlannerRepository


@dataclass(slots=True)
class SettingsContext:
    session: AsyncSession
    repository: SettingsRepository
    service: SettingsService


@dataclass(slots=True)
class DashboardContext:
    session: AsyncSession
    repository: DashboardRepository
    service: DashboardService


@dataclass(slots=True)
class FirewallContext:
    session: AsyncSession
    repository: FirewallRepository
    service: FirewallService


@dataclass(slots=True)
class StickySessionsContext:
    session: AsyncSession
    repository: StickySessionsRepository
    settings_repository: SettingsRepository
    service: StickySessionsService


@dataclass(slots=True)
class ReportsContext:
    session: AsyncSession
    repository: ReportsRepository
    service: ReportsService


def get_accounts_context(
    session: AsyncSession = Depends(get_session),
) -> AccountsContext:
    repository = AccountsRepository(session)
    usage_repository = UsageRepository(session)
    additional_usage_repository = AdditionalUsageRepository(session)
    limit_warmup_repository = LimitWarmupRepository(session)
    service = AccountsService(
        repository,
        usage_repository,
        additional_usage_repository,
        limit_warmup_repository,
        auth_manager=AuthManager(repository, refresh_repo_factory=_accounts_repo_context),
    )
    return AccountsContext(
        session=session,
        repository=repository,
        service=service,
    )


def get_agent_provider_accounts_context(
    session: AsyncSession = Depends(get_session),
) -> AgentProviderAccountsContext:
    repository = AgentProviderAccountsRepository(session)
    service = AgentProviderAccountsService(repository)
    return AgentProviderAccountsContext(session=session, repository=repository, service=service)


def get_agent_provider_routing_context(
    session: AsyncSession = Depends(get_session),
) -> AgentProviderRoutingContext:
    repository = AgentProviderRoutingRepository(session)
    service = AgentProviderRoutingService(repository)
    return AgentProviderRoutingContext(session=session, repository=repository, service=service)


def get_agent_provider_runtime_context(
    session: AsyncSession = Depends(get_session),
) -> AgentProviderRuntimeContext:
    routing_repository = AgentProviderRoutingRepository(session)
    routing_service = AgentProviderRoutingService(routing_repository)
    api_keys_repository = ApiKeysRepository(session)
    request_logs_repository = RequestLogsRepository(session)
    gemini_service = GeminiRuntimeService(
        routing_service,
        api_key_service=cast(ApiKeyUsagePort, ApiKeysService(api_keys_repository)),
        request_logs=cast(RequestLogWriterPort, request_logs_repository),
    )
    antigravity_service = AntigravityHarnessService(
        routing_service,
        request_logs=cast(RequestLogWriterPort, request_logs_repository),
    )
    antigravity_managed_service = AntigravityManagedAgentService(
        routing_service,
        api_key_service=cast(ApiKeyUsagePort, ApiKeysService(api_keys_repository)),
        request_logs=cast(RequestLogWriterPort, request_logs_repository),
    )
    return AgentProviderRuntimeContext(
        session=session,
        routing_repository=routing_repository,
        routing_service=routing_service,
        api_keys_repository=api_keys_repository,
        request_logs_repository=request_logs_repository,
        gemini_service=gemini_service,
        antigravity_service=antigravity_service,
        antigravity_managed_service=antigravity_managed_service,
    )


def get_agent_providers_context(
    session: AsyncSession = Depends(get_session),
) -> AgentProvidersContext:
    service = AgentProviderOverviewService(
        codex_accounts=AccountsRepository(session),
        provider_accounts=AgentProviderAccountsRepository(session),
        provider_routing=AgentProviderRoutingRepository(session),
        request_logs=RequestLogsRepository(session),
    )
    return AgentProvidersContext(session=session, service=service)


def get_audit_context(
    session: AsyncSession = Depends(get_session),
) -> AuditContext:
    repository = AuditRepository(session)
    service = AuditLogsService(repository)
    return AuditContext(session=session, repository=repository, service=service)


def get_usage_context(
    session: AsyncSession = Depends(get_session),
) -> UsageContext:
    usage_repository = UsageRepository(session)
    request_logs_repository = RequestLogsRepository(session)
    accounts_repository = AccountsRepository(session)
    service = UsageService(
        usage_repository,
        request_logs_repository,
        accounts_repository,
    )
    return UsageContext(
        session=session,
        usage_repository=usage_repository,
        service=service,
    )


@asynccontextmanager
async def _accounts_repo_context() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


@asynccontextmanager
async def _proxy_repo_context() -> AsyncIterator[ProxyRepositories]:
    async with get_background_session() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            request_logs=RequestLogsRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=ApiKeysRepository(session),
            additional_usage=AdditionalUsageRepository(session),
            quota_planner=QuotaPlannerRepository(session),
        )


def get_oauth_context(
    session: AsyncSession = Depends(get_session),
) -> OauthContext:
    accounts_repository = AccountsRepository(session)
    return OauthContext(service=OauthService(accounts_repository, repo_factory=_accounts_repo_context))


def get_dashboard_auth_context(
    session: AsyncSession = Depends(get_session),
) -> DashboardAuthContext:
    repository = DashboardAuthRepository(session)
    service = DashboardAuthService(cast(DashboardAuthRepositoryProtocol, repository), get_dashboard_session_store())
    return DashboardAuthContext(session=session, repository=repository, service=service)


def get_proxy_context(request: Request) -> ProxyContext:
    service = get_proxy_service_for_app(request.app)
    return ProxyContext(service=service)


def get_proxy_service_for_app(app: FastAPI) -> ProxyService:
    state = app.state
    service = getattr(state, "proxy_service", None)
    if not isinstance(service, ProxyService):
        service = ProxyService(repo_factory=_proxy_repo_context)
        setattr(state, "proxy_service", service)
    return service


def get_proxy_websocket_context(websocket: WebSocket) -> ProxyContext:
    service = get_proxy_service_for_app(websocket.app)
    return ProxyContext(service=service)


def get_api_keys_context(
    session: AsyncSession = Depends(get_session),
) -> ApiKeysContext:
    repository = ApiKeysRepository(session)
    usage_repository = UsageRepository(session)
    service = ApiKeysService(repository, usage_repository=usage_repository)
    return ApiKeysContext(session=session, repository=repository, service=service)


def get_request_logs_context(
    session: AsyncSession = Depends(get_session),
) -> RequestLogsContext:
    repository = RequestLogsRepository(session)
    service = RequestLogsService(repository)
    return RequestLogsContext(session=session, repository=repository, service=service)


def get_quota_planner_context(
    session: AsyncSession = Depends(get_session),
) -> QuotaPlannerContext:
    repository = QuotaPlannerRepository(session)
    return QuotaPlannerContext(session=session, repository=repository)


def get_settings_context(
    session: AsyncSession = Depends(get_session),
) -> SettingsContext:
    repository = SettingsRepository(session)
    service = SettingsService(repository)
    return SettingsContext(session=session, repository=repository, service=service)


def get_dashboard_context(
    session: AsyncSession = Depends(get_session),
) -> DashboardContext:
    repository = DashboardRepository(session)
    service = DashboardService(repository)
    return DashboardContext(session=session, repository=repository, service=service)


def get_firewall_context(
    session: AsyncSession = Depends(get_session),
) -> FirewallContext:
    repository = FirewallRepository(session)
    service = FirewallService(cast(FirewallRepositoryPort, repository))
    return FirewallContext(session=session, repository=repository, service=service)


def get_sticky_sessions_context(
    session: AsyncSession = Depends(get_session),
) -> StickySessionsContext:
    repository = StickySessionsRepository(session)
    settings_repository = SettingsRepository(session)
    service = StickySessionsService(repository, settings_repository)
    return StickySessionsContext(
        session=session,
        repository=repository,
        settings_repository=settings_repository,
        service=service,
    )


def get_reports_context(
    session: AsyncSession = Depends(get_session),
) -> ReportsContext:
    repository = ReportsRepository(session)
    service = ReportsService(repository)
    return ReportsContext(session=session, repository=repository, service=service)
