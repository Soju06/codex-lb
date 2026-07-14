from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.clients.proxy import ProxyResponseError, is_confirmed_pre_dispatch_transport_error
from app.db.models import Account
from app.modules.proxy.load_balancer import AccountLease


@dataclass
class _HTTPBridgePreDispatchFailover:
    excluded_account_ids: set[str]
    preferred_account_id: str | None
    reallocate_sticky: bool
    last_error: ProxyResponseError | None = None

    async def handle(
        self,
        service: Any,
        account: Account,
        lease: AccountLease | None,
        exc: ProxyResponseError,
        *,
        required_account: bool,
    ) -> bool:
        if not is_confirmed_pre_dispatch_transport_error(exc):
            return False
        await service._load_balancer.release_account_lease(lease)
        await service._load_balancer.record_error_backoff(account)
        if required_account:
            raise exc
        self.last_error = exc
        self.excluded_account_ids.add(account.id)
        self.preferred_account_id = None
        self.reallocate_sticky = True
        return True
