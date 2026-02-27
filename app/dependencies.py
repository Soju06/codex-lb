from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_background_session, get_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.service import AccountsService
from app.modules.anthropic.repository import AnthropicRepository
from app.modules.anthropic.service import AnthropicService
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import DashboardService
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_auth.service import DashboardAuthService, get_dashboard_session_store
from app.modules.oauth.service import OauthService
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.service import ProxyService
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.request_logs.service import RequestLogsService
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.service import SettingsService
from app.modules.usage.repository import UsageRepository
from app.modules.usage.service import UsageService


@dataclass(slots=True)
class AccountsContext:
    session: AsyncSession
    repository: AccountsRepository
    service: AccountsService


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
class AnthropicContext:
    service: AnthropicService


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
class SettingsContext:
    session: AsyncSession
    repository: SettingsRepository
    service: SettingsService


@dataclass(slots=True)
class DashboardContext:
    session: AsyncSession
    repository: DashboardRepository
    service: DashboardService


def get_accounts_context(
    session: AsyncSession = Depends(get_session),
) -> AccountsContext:
    repository = AccountsRepository(session)
    usage_repository = UsageRepository(session)
    service = AccountsService(repository, usage_repository)
    return AccountsContext(
        session=session,
        repository=repository,
        service=service,
    )


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
    service = DashboardAuthService(repository, get_dashboard_session_store())
    return DashboardAuthContext(session=session, repository=repository, service=service)


def get_proxy_context(request: Request) -> ProxyContext:
    service = getattr(request.app.state, "proxy_service", None)
    if not isinstance(service, ProxyService):
        service = ProxyService(repo_factory=_proxy_repo_context)
        request.app.state.proxy_service = service
    return ProxyContext(service=service)


def get_anthropic_context(
    session: AsyncSession = Depends(get_session),
) -> AnthropicContext:
    repository = AnthropicRepository(session)
    service = AnthropicService(repository)
    return AnthropicContext(service=service)


def get_api_keys_context(
    session: AsyncSession = Depends(get_session),
) -> ApiKeysContext:
    repository = ApiKeysRepository(session)
    service = ApiKeysService(repository)
    return ApiKeysContext(session=session, repository=repository, service=service)


def get_request_logs_context(
    session: AsyncSession = Depends(get_session),
) -> RequestLogsContext:
    repository = RequestLogsRepository(session)
    service = RequestLogsService(repository)
    return RequestLogsContext(session=session, repository=repository, service=service)


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
