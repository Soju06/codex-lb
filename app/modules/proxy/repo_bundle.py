from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import AsyncContextManager

from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import UsageRepository


@dataclass(slots=True)
class ProxyRepositories:
    accounts: AccountsRepository
    usage: UsageRepository
    request_logs: RequestLogsRepository
    sticky_sessions: StickySessionsRepository
    settings: SettingsRepository


ProxyRepoFactory = Callable[[], AsyncContextManager[ProxyRepositories]]
