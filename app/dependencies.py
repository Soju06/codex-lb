from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal, _safe_close, _safe_rollback, get_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.service import AccountsService
from app.modules.oauth.service import OauthService
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
class ProxyContext:
    service: ProxyService


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


def get_accounts_context(
    session: AsyncSession = Depends(get_session),
) -> AccountsContext:
    repository = AccountsRepository(session)
    usage_repository = UsageRepository(session)
    request_logs_repository = RequestLogsRepository(session)
    service = AccountsService(repository, usage_repository, request_logs_repository)
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
        refresh_repo_factory=_usage_refresh_context,
    )
    return UsageContext(
        session=session,
        usage_repository=usage_repository,
        service=service,
    )


@asynccontextmanager
async def _accounts_repo_context() -> AsyncIterator[AccountsRepository]:
    session = SessionLocal()
    try:
        yield AccountsRepository(session)
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)


@asynccontextmanager
async def _usage_refresh_context() -> AsyncIterator[tuple[UsageRepository, AccountsRepository]]:
    session = SessionLocal()
    try:
        yield UsageRepository(session), AccountsRepository(session)
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)


def get_oauth_context(
    session: AsyncSession = Depends(get_session),
) -> OauthContext:
    accounts_repository = AccountsRepository(session)
    return OauthContext(service=OauthService(accounts_repository, repo_factory=_accounts_repo_context))


def get_proxy_context(
    session: AsyncSession = Depends(get_session),
) -> ProxyContext:
    accounts_repository = AccountsRepository(session)
    usage_repository = UsageRepository(session)
    request_logs_repository = RequestLogsRepository(session)
    sticky_repository = StickySessionsRepository(session)
    settings_repository = SettingsRepository(session)
    service = ProxyService(
        accounts_repository,
        usage_repository,
        request_logs_repository,
        sticky_repository,
        settings_repository,
    )
    return ProxyContext(service=service)


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
