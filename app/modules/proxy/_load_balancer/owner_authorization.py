from __future__ import annotations

import logging

from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.usage.authorization import OwnerAuthorization, OwnerAuthorizationKind, load_owner_authorization

logger = logging.getLogger("app.modules.proxy.load_balancer")


async def load_fresh_owner_authorization(
    repo_factory: ProxyRepoFactory,
    account_id: str,
    *,
    refresh_interval_seconds: int,
) -> OwnerAuthorization:
    """Load one owner's policy and usage windows in one database statement."""
    try:
        async with repo_factory() as repos:
            return await load_owner_authorization(
                repos.usage, account_id, refresh_interval_seconds=refresh_interval_seconds
            )
    except Exception:
        logger.warning("Fresh owner authorization failed account_id=%s", account_id, exc_info=True)
        return OwnerAuthorization(OwnerAuthorizationKind.AUTHORIZATION_FAILED)
