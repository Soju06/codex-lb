from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import AsyncContextManager

from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.model_overrides.repository import ModelOverridesRepository
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository


@dataclass(slots=True)
class ProxyRepositories:
    accounts: AccountsRepository
    usage: UsageRepository
    request_logs: RequestLogsRepository
    sticky_sessions: StickySessionsRepository
    api_keys: ApiKeysRepository
    model_overrides: ModelOverridesRepository


ProxyRepoFactory = Callable[[], AsyncContextManager[ProxyRepositories]]
