from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import AsyncContextManager

from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository


@dataclass(slots=True)
class ProxyRepositories:
    accounts: AccountsRepository
    usage: UsageRepository
    request_logs: RequestLogsRepository
    sticky_sessions: StickySessionsRepository
    api_keys: ApiKeysRepository
    additional_usage: AdditionalUsageRepository
    quota_planner: QuotaPlannerRepository | None = None


ProxyRepoFactory = Callable[[], AsyncContextManager[ProxyRepositories]]
