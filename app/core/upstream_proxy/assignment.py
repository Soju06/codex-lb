from __future__ import annotations

from sqlalchemy import and_, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.account_identity_lock import advisory_lock_key
from app.db.models import Account, AccountProxyBinding, ProxyEndpoint, ProxyPool, ProxyPoolMember

_POSTGRES_AUTO_ASSIGNMENT_LOCK_KEY = advisory_lock_key(
    "upstream-proxy",
    "automatic-account-pool-assignment",
)


async def assign_new_account_to_proxy_pool(session: AsyncSession, account: Account) -> bool:
    """Attach a least-loaded usable proxy pool to one pending new account.

    The caller owns the transaction and MUST call this only for a genuinely new
    ``Account`` row. Returning ``False`` means no structurally usable pool was
    configured; this helper never commits or changes an existing binding.
    """

    await _serialize_postgresql_auto_assignment(session)

    active_binding_count = func.count(AccountProxyBinding.id)
    usable_member_exists = exists(
        select(ProxyPoolMember.id)
        .join(ProxyEndpoint, ProxyEndpoint.id == ProxyPoolMember.endpoint_id)
        .where(
            ProxyPoolMember.pool_id == ProxyPool.id,
            ProxyPoolMember.is_active.is_(True),
            ProxyEndpoint.is_active.is_(True),
        )
    )
    result = await session.execute(
        select(ProxyPool.id)
        .outerjoin(
            AccountProxyBinding,
            and_(
                AccountProxyBinding.pool_id == ProxyPool.id,
                AccountProxyBinding.is_active.is_(True),
            ),
        )
        .where(ProxyPool.is_active.is_(True), usable_member_exists)
        .group_by(ProxyPool.id, ProxyPool.created_at)
        .order_by(active_binding_count.asc(), ProxyPool.created_at.asc(), ProxyPool.id.asc())
        .limit(1)
    )
    pool_id = result.scalar_one_or_none()
    if pool_id is None:
        return False

    account.proxy_binding = AccountProxyBinding(pool_id=pool_id, is_active=True)
    return True


async def _serialize_postgresql_auto_assignment(session: AsyncSession) -> None:
    """Serialize the short least-loaded select-and-insert section on PostgreSQL."""

    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _POSTGRES_AUTO_ASSIGNMENT_LOCK_KEY},
    )
