from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, Index
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Account, AccountStatus, Base

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # The ``ck_accounts_claude_rt_required`` constraint is migration-owned
        # (see :class:`Account.__table_args__`), so ``create_all`` does not
        # emit it on the model side. Add it here via the same
        # ``batch_alter_table`` path the production migration uses, so the
        # in-memory DB enforces the invariant the rejection tests assert on.
        # ``Operations`` needs a sync connection, so wrap in ``run_sync``.
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        def _add_constraint(sync_conn: Any) -> None:
            ctx = MigrationContext.configure(sync_conn)
            ops = Operations(ctx)
            with ops.batch_alter_table("accounts") as batch_op:
                batch_op.create_check_constraint(
                    "ck_accounts_claude_rt_required",
                    "((provider = 'claude') AND (claude_refresh_token_encrypted IS NOT NULL)) "
                    "OR ((provider = 'codex') AND (claude_refresh_token_encrypted IS NULL))",
                )

        await conn.run_sync(_add_constraint)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def test_account_provider_column_is_not_nullable() -> None:
    column = Account.__table__.c.provider
    assert column is not None
    assert column.nullable is False


def test_account_provider_has_check_constraint_constraining_values() -> None:
    table = Account.__table__
    check_constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]  # ty:ignore[unresolved-attribute]
    provider_checks = [c for c in check_constraints if "provider" in str(c.sqltext).lower()]
    assert provider_checks, "expected a CHECK constraint referencing 'provider'"
    # The constraint name must be stable so the alembic migration can drop it.
    assert any(c.name == "ck_accounts_provider" for c in provider_checks)


@pytest.mark.asyncio
async def test_account_provider_value_must_be_codex_or_claude(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        account = Account(
            id="acc-bad",
            email="bad@example.com",
            plan_type="plus",
            codex_installation_id="11111111-1111-1111-1111-111111111111",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"r",
            id_token_encrypted=b"i",
            last_refresh=datetime.now(timezone.utc),
            provider="bogus",  # type: ignore[arg-type]
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        with pytest.raises(Exception):
            await session.flush()


def test_account_has_claude_columns() -> None:
    names = {c.name for c in Account.__table__.columns}
    expected = {
        "claude_account_uuid",
        "claude_refresh_token_encrypted",
        "claude_access_token_encrypted",
        "claude_access_token_expires_at",
        "claude_scopes",
        "claude_user_email",
        "claude_user_organization_uuid",
        "rate_limit_requests_remaining",
        "rate_limit_requests_reset_at",
        "rate_limit_input_tokens_remaining",
        "rate_limit_input_tokens_reset_at",
        "rate_limit_output_tokens_remaining",
        "rate_limit_output_tokens_reset_at",
        "rate_limit_status",
    }
    missing = expected - names
    assert not missing, f"missing columns: {sorted(missing)}"


def test_claude_account_uuid_partial_unique_index() -> None:
    table = Account.__table__
    indexes = [idx for idx in table.indexes if isinstance(idx, Index)]  # ty:ignore[unresolved-attribute]
    partial = [idx for idx in indexes if idx.unique and any(col.name == "claude_account_uuid" for col in idx.columns)]
    assert partial, "expected a partial unique index on claude_account_uuid"
    idx = partial[0]
    assert idx.name == "uq_accounts_claude_uuid"
    sqlite_where = (idx.dialect_options.get("sqlite") or {}).get("where")
    postgres_where = (idx.dialect_options.get("postgresql") or {}).get("where")
    assert sqlite_where is not None and "claude" in str(sqlite_where).lower()
    assert postgres_where is not None and "claude" in str(postgres_where).lower()


def test_account_email_is_nullable() -> None:
    column = Account.__table__.c.email
    assert column.nullable is True


def test_account_owns_claude_refresh_token_check_via_migration_only() -> None:
    """The ``ck_accounts_claude_rt_required`` constraint is intentionally NOT
    declared on the ORM model.

    The model previously declared it as a ``CheckConstraint``, but
    ``Base.metadata.create_all`` then emitted it on PostgreSQL test fixtures
    where the migration ``20260701_010000_enforce_claude_rt_and_codex_email_invariants``
    also tries to emit it — ``DuplicateObject`` on PostgreSQL. Moving the
    declaration off the model makes the migration chain the sole owner of
    the constraint (it owns the lifecycle on upgrade / downgrade and on the
    bootstrap-created-schema reconciliation in
    ``20260701_020000_reconcile_claude_rt_check_predicate``). The constraint
    is still enforced at the DB level; the model just no longer
    double-declares it.
    """
    table = Account.__table__
    check_constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]  # ty:ignore[unresolved-attribute]
    rt_checks = [c for c in check_constraints if "claude_refresh_token_encrypted" in str(c.sqltext).lower()]
    assert not rt_checks, (
        "ck_accounts_claude_rt_required must NOT be declared on the ORM model; "
        "the migration chain owns it. Found: "
        f"{[c.name for c in rt_checks]}"
    )


@pytest.mark.asyncio
async def test_codex_account_with_claude_refresh_token_is_rejected(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spec: Adding a Codex account with a non-null refresh token is rejected.

    When the application attempts to insert a Codex account row with
    ``claude_refresh_token_encrypted IS NOT NULL``, the database CHECK
    constraint rejects the insert.
    """
    async with async_session_factory() as session:
        account = Account(
            id="acc-codex-with-rt",
            email="codex-rt@example.com",
            plan_type="plus",
            codex_installation_id="22222222-2222-2222-2222-222222222222",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"r",
            id_token_encrypted=b"i",
            last_refresh=datetime.now(timezone.utc),
            provider="codex",
            claude_refresh_token_encrypted=b"should-not-be-here",
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        with pytest.raises(Exception):
            await session.flush()


@pytest.mark.asyncio
async def test_claude_account_with_refresh_token_is_accepted(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Control: a Claude row with a refresh token satisfies the CHECK constraint."""
    async with async_session_factory() as session:
        account = Account(
            id="acc-claude-with-rt",
            plan_type="max",
            codex_installation_id="33333333-3333-3333-3333-333333333333",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"r",
            id_token_encrypted=b"i",
            last_refresh=datetime.now(timezone.utc),
            provider="claude",
            claude_account_uuid="claude-uuid-1",
            claude_refresh_token_encrypted=b"ciphertext-blob",
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        await session.flush()


@pytest.mark.asyncio
async def test_claude_account_without_refresh_token_is_rejected(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spec: a Claude row without a refresh token violates the CHECK constraint."""
    async with async_session_factory() as session:
        account = Account(
            id="acc-claude-no-rt",
            plan_type="max",
            codex_installation_id="44444444-4444-4444-4444-444444444444",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"r",
            id_token_encrypted=b"i",
            last_refresh=datetime.now(timezone.utc),
            provider="claude",
            claude_account_uuid="claude-uuid-2",
            claude_refresh_token_encrypted=None,
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        with pytest.raises(Exception):
            await session.flush()


def test_accounts_codex_email_partial_unique_index() -> None:
    table = Account.__table__
    indexes = [idx for idx in table.indexes if isinstance(idx, Index)]  # ty:ignore[unresolved-attribute]
    partial = [idx for idx in indexes if idx.unique and any(col.name == "email" for col in idx.columns)]
    assert partial, "expected a partial unique index on email"
    codex_email_index = next(
        (idx for idx in partial if idx.name == "uq_accounts_codex_email"),
        None,
    )
    assert codex_email_index is not None, "expected uq_accounts_codex_email"
    sqlite_where = (codex_email_index.dialect_options.get("sqlite") or {}).get("where")
    postgres_where = (codex_email_index.dialect_options.get("postgresql") or {}).get("where")
    assert sqlite_where is not None and "codex" in str(sqlite_where).lower()
    assert postgres_where is not None and "codex" in str(postgres_where).lower()


@pytest.mark.asyncio
async def test_two_codex_accounts_with_same_email_are_rejected(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spec: Two Codex accounts with the same email are rejected.

    The account-routing spec requires provider-aware identity: two Codex rows
    with identical ``email`` must violate the partial unique index.
    """
    async with async_session_factory() as session:
        first = Account(
            id="acc-codex-email-1",
            email="dup@example.com",
            plan_type="plus",
            codex_installation_id="55555555-5555-5555-5555-555555555555",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"r",
            id_token_encrypted=b"i",
            last_refresh=datetime.now(timezone.utc),
            provider="codex",
            status=AccountStatus.ACTIVE,
        )
        second = Account(
            id="acc-codex-email-2",
            email="dup@example.com",
            plan_type="plus",
            codex_installation_id="66666666-6666-6666-6666-666666666666",
            access_token_encrypted=b"a2",
            refresh_token_encrypted=b"r2",
            id_token_encrypted=b"i2",
            last_refresh=datetime.now(timezone.utc),
            provider="codex",
            status=AccountStatus.ACTIVE,
        )
        session.add(first)
        await session.flush()
        session.add(second)
        with pytest.raises(Exception):
            await session.flush()


@pytest.mark.asyncio
async def test_claude_account_email_does_not_collide_with_codex_email(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two rows sharing an email but different providers must NOT collide.

    The partial UNIQUE index only applies to ``provider='codex'``, so a Claude
    row may share an email with a Codex row.
    """
    async with async_session_factory() as session:
        codex = Account(
            id="acc-codex-shared-email",
            email="shared@example.com",
            plan_type="plus",
            codex_installation_id="77777777-7777-7777-7777-777777777777",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"r",
            id_token_encrypted=b"i",
            last_refresh=datetime.now(timezone.utc),
            provider="codex",
            status=AccountStatus.ACTIVE,
        )
        claude = Account(
            id="acc-claude-shared-email",
            email="shared@example.com",
            plan_type="max",
            codex_installation_id="88888888-8888-8888-8888-888888888888",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"r",
            id_token_encrypted=b"i",
            last_refresh=datetime.now(timezone.utc),
            provider="claude",
            claude_account_uuid="claude-uuid-shared",
            claude_refresh_token_encrypted=b"ciphertext-blob",
            status=AccountStatus.ACTIVE,
        )
        session.add(codex)
        await session.flush()
        session.add(claude)
        await session.flush()
