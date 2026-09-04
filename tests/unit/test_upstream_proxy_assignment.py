from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.upstream_proxy.assignment import (
    _POSTGRES_AUTO_ASSIGNMENT_LOCK_KEY,
    _serialize_postgresql_auto_assignment,
    assign_new_account_to_proxy_pool,
)
from app.db.models import (
    Account,
    AccountProxyBinding,
    AccountStatus,
    Base,
    ProxyEndpoint,
    ProxyPool,
    ProxyPoolMember,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _account(account_id: str) -> Account:
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2026, 1, 1),
        status=AccountStatus.ACTIVE,
    )


def _pool(
    pool_id: str,
    *,
    created_at: datetime,
    pool_active: bool = True,
    member_active: bool = True,
    endpoint_active: bool = True,
) -> tuple[ProxyPool, ProxyEndpoint, ProxyPoolMember]:
    pool = ProxyPool(id=pool_id, name=pool_id, is_active=pool_active, created_at=created_at)
    endpoint = ProxyEndpoint(
        id=f"{pool_id}_endpoint",
        name=f"{pool_id} endpoint",
        scheme="http",
        host="proxy.test",
        port=8080,
        is_active=endpoint_active,
    )
    member = ProxyPoolMember(
        id=f"{pool_id}_member",
        pool=pool,
        endpoint=endpoint,
        is_active=member_active,
    )
    return pool, endpoint, member


@pytest.mark.asyncio
async def test_assignment_filters_unusable_pools_and_ignores_inactive_bindings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        created_at = datetime(2026, 1, 1)
        inactive_pool = _pool("inactive_pool", created_at=created_at, pool_active=False)
        inactive_member = _pool("inactive_member", created_at=created_at, member_active=False)
        inactive_endpoint = _pool("inactive_endpoint", created_at=created_at, endpoint_active=False)
        loaded_pool = _pool("loaded_pool", created_at=datetime(2026, 1, 2))
        available_pool = _pool("available_pool", created_at=datetime(2026, 1, 3))
        active_owner = _account("active_owner")
        inactive_owner = _account("inactive_owner")
        session.add_all(
            [
                *inactive_pool,
                *inactive_member,
                *inactive_endpoint,
                *loaded_pool,
                *available_pool,
                active_owner,
                inactive_owner,
                AccountProxyBinding(account=active_owner, pool_id="loaded_pool", is_active=True),
                AccountProxyBinding(account=inactive_owner, pool_id="available_pool", is_active=False),
            ]
        )
        await session.commit()

        new_account = _account("new_account")
        session.add(new_account)
        assigned = await assign_new_account_to_proxy_pool(session, new_account)

    assert assigned is True
    assert new_account.proxy_binding is not None
    assert new_account.proxy_binding.pool_id == "available_pool"
    assert new_account.proxy_binding.is_active is True


@pytest.mark.asyncio
async def test_sequential_assignments_balance_usable_pools_with_stable_tie_break(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first_pool = _pool("first_pool", created_at=datetime(2026, 1, 1))
        second_pool = _pool("second_pool", created_at=datetime(2026, 1, 2))
        session.add_all([*first_pool, *second_pool])
        await session.commit()

        assigned_pool_ids: list[str] = []
        for index in range(5):
            account = _account(f"account_{index}")
            session.add(account)
            assert await assign_new_account_to_proxy_pool(session, account) is True
            assert account.proxy_binding is not None
            assigned_pool_ids.append(account.proxy_binding.pool_id)
            await session.commit()

        count_rows = (
            await session.execute(
                select(AccountProxyBinding.pool_id, func.count(AccountProxyBinding.id))
                .where(AccountProxyBinding.is_active.is_(True))
                .group_by(AccountProxyBinding.pool_id)
            )
        ).tuples()
        counts = {pool_id: count for pool_id, count in count_rows}

    assert assigned_pool_ids == ["first_pool", "second_pool", "first_pool", "second_pool", "first_pool"]
    assert counts == {"first_pool": 3, "second_pool": 2}


@pytest.mark.asyncio
async def test_assignment_leaves_new_account_unbound_without_usable_pool(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add_all(_pool("inactive_endpoint", created_at=datetime(2026, 1, 1), endpoint_active=False))
        account = _account("unbound_account")
        session.add(account)

        assigned = await assign_new_account_to_proxy_pool(session, account)
        binding_count = await session.scalar(
            select(func.count(AccountProxyBinding.id)).where(AccountProxyBinding.account_id == account.id)
        )

    assert assigned is False
    assert binding_count == 0


@pytest.mark.asyncio
async def test_postgresql_assignment_uses_transaction_advisory_lock() -> None:
    session = AsyncMock(spec=AsyncSession)
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind.return_value = bind

    await _serialize_postgresql_auto_assignment(session)

    statement, parameters = session.execute.await_args.args
    assert str(statement) == "SELECT pg_advisory_xact_lock(:lock_key)"
    assert parameters == {"lock_key": _POSTGRES_AUTO_ASSIGNMENT_LOCK_KEY}
