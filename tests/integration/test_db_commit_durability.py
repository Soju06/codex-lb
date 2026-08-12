from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey
from app.db.session import SessionLocal, engine, relax_commit_durability
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.last_used_coalescer import ApiKeyLastUsedCoalescer
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository, UsageWindowWrite

pytestmark = pytest.mark.integration

_SET_LOCAL_FRAGMENT = "SET LOCAL synchronous_commit"


def _require_postgres() -> None:
    url = os.environ.get("CODEX_LB_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("requires CODEX_LB_TEST_DATABASE_URL=postgresql+asyncpg://...")


@contextmanager
def _captured_statements() -> Iterator[list[str]]:
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)


def _assert_relaxed_before(statements: list[str], statement_fragment: str) -> None:
    relax_indexes = [index for index, statement in enumerate(statements) if _SET_LOCAL_FRAGMENT in statement]
    target_indexes = [index for index, statement in enumerate(statements) if statement_fragment in statement]
    assert relax_indexes, f"no durability relaxation captured in {statements!r}"
    assert target_indexes, f"no {statement_fragment!r} captured in {statements!r}"
    assert relax_indexes[0] < target_indexes[0], (
        f"durability relaxation must precede {statement_fragment!r} within the transaction: {statements!r}"
    )


def _assert_not_relaxed(statements: list[str]) -> None:
    assert all(_SET_LOCAL_FRAGMENT not in statement for statement in statements), statements


def _assert_executed_without_relaxation(statements: list[str], statement_fragment: str) -> None:
    assert any(statement_fragment in statement for statement in statements), (
        f"no {statement_fragment!r} captured in {statements!r}"
    )
    _assert_not_relaxed(statements)


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _make_api_key(key_id: str) -> ApiKey:
    return ApiKey(
        id=key_id,
        name=f"key-{key_id}",
        key_hash=f"hash-{key_id}",
        key_prefix="sk-test",
    )


@pytest.mark.asyncio
async def test_relax_commit_durability_applies_within_the_write_transaction() -> None:
    _require_postgres()
    async with SessionLocal() as session:
        await relax_commit_durability(session)
        # Session autobegin opened the transaction at the SET LOCAL statement
        # itself — the in-transaction guarantee that makes SET LOCAL apply at
        # all (outside a transaction PostgreSQL only emits a WARNING).
        assert session.in_transaction()
        value = (await session.execute(text("SHOW synchronous_commit"))).scalar_one()
        assert value == "off"
        await session.rollback()


@pytest.mark.asyncio
async def test_relaxed_durability_reverts_to_session_default_after_commit_and_rollback() -> None:
    _require_postgres()
    async with engine.connect() as conn:
        transaction = await conn.begin()
        await conn.execute(text("SET LOCAL synchronous_commit = off"))
        assert (await conn.execute(text("SHOW synchronous_commit"))).scalar_one() == "off"
        await transaction.commit()
        # Same physical connection: COMMIT reverted the transaction-local
        # override back to the session default.
        assert (await conn.execute(text("SHOW synchronous_commit"))).scalar_one() == "on"
        await conn.rollback()

        transaction = await conn.begin()
        await conn.execute(text("SET LOCAL synchronous_commit = off"))
        assert (await conn.execute(text("SHOW synchronous_commit"))).scalar_one() == "off"
        await transaction.rollback()
        assert (await conn.execute(text("SHOW synchronous_commit"))).scalar_one() == "on"
        await conn.rollback()


@pytest.mark.asyncio
async def test_set_local_outside_a_transaction_does_not_stick() -> None:
    """The trap the helper exists to avoid: SET LOCAL without a transaction.

    PostgreSQL only emits ``WARNING: SET LOCAL can only be used in transaction
    blocks`` and applies nothing. The helper always runs through the write
    transaction's session, where autobegin guarantees an open transaction.
    """
    _require_postgres()
    async with engine.connect() as conn:
        autocommit_conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit_conn.execute(text("SET LOCAL synchronous_commit = off"))
        value = (await autocommit_conn.execute(text("SHOW synchronous_commit"))).scalar_one()
        assert value == "on"


@pytest.mark.asyncio
async def test_request_log_insert_transaction_relaxes_commit_durability(db_setup) -> None:
    _require_postgres()
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        with _captured_statements() as statements:
            await repo.add_log(
                account_id=None,
                request_id=f"req-{uuid4().hex}",
                model="gpt-5",
                input_tokens=10,
                output_tokens=5,
                latency_ms=42,
                status="success",
                error_code=None,
            )
        _assert_relaxed_before(statements, "INSERT INTO request_logs")


@pytest.mark.asyncio
async def test_usage_reservation_creation_and_settlement_keep_full_commit_durability(db_setup) -> None:
    """Regression: reservation accounting must NOT relax commit durability
    (adversarial review P1 on PR #1628). On external/HA PostgreSQL a failover
    does not kill in-flight application requests, so an acked-but-lost
    settlement commit would strand the reservation as ``reserved`` and the
    stale release would reverse the counters for a request that completed.
    """
    _require_postgres()
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        key_id = f"key-{uuid4().hex[:8]}"
        await repo.create(_make_api_key(key_id))

        reservation_id = f"res-{uuid4().hex[:8]}"
        used_at = utcnow()
        coalescer = ApiKeyLastUsedCoalescer()
        await coalescer.record(key_id, used_at)
        with _captured_statements() as statements:
            await repo.create_usage_reservation(reservation_id, key_id=key_id, model="gpt-5", items=[])
            await repo.commit()
        _assert_executed_without_relaxation(statements, "INSERT INTO api_key_usage_reservations")

        with _captured_statements() as statements:
            await repo.settle_usage_reservation(
                reservation_id,
                status="finalized",
                input_tokens=10,
                output_tokens=5,
                cached_input_tokens=0,
                cost_microdollars=0,
            )
            await repo.commit()
        _assert_executed_without_relaxation(statements, "UPDATE api_key_usage_reservations")
        assert coalescer.pending_snapshot() == {key_id: used_at}
        assert await coalescer.flush() == 1
        updated = await repo.get_by_id(key_id)
        assert updated is not None
        assert updated.last_used_at == used_at


@pytest.mark.asyncio
async def test_stale_usage_reservation_release_keeps_full_commit_durability(db_setup) -> None:
    """Regression: the scheduler's stale-reservation release settles the same
    per-request accounting rows as the request-path settlement, so each batch
    must keep the same full durability — durability of a release must not
    depend on which path fires (adversarial review P1 on PR #1628).
    """
    _require_postgres()
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        key_id = f"key-{uuid4().hex[:8]}"
        await repo.create(_make_api_key(key_id))

        reservation_id = f"res-{uuid4().hex[:8]}"
        await repo.create_usage_reservation(reservation_id, key_id=key_id, model="gpt-5", items=[])
        await repo.commit()

        # A cutoff in the future makes the freshly created reservation stale.
        with _captured_statements() as statements:
            released_count = await repo.release_stale_usage_reservations(cutoff=utcnow() + timedelta(hours=1))

        assert released_count == 1
        _assert_executed_without_relaxation(statements, "UPDATE api_key_usage_reservations")


@pytest.mark.asyncio
async def test_usage_history_appends_relax_commit_durability(db_setup) -> None:
    _require_postgres()
    async with SessionLocal() as session:
        account_id = f"acc-{uuid4().hex[:8]}"
        await AccountsRepository(session).upsert(_make_account(account_id))

        usage_repo = UsageRepository(session)
        with _captured_statements() as statements:
            await usage_repo.add_entry(account_id, 12.5)
        _assert_relaxed_before(statements, "INSERT INTO usage_history")

        with _captured_statements() as statements:
            await usage_repo.add_account_snapshot(
                account_id,
                [UsageWindowWrite(window="primary", used_percent=20.0)],
            )
        _assert_relaxed_before(statements, "INSERT INTO usage_history")

        additional_repo = AdditionalUsageRepository(session)
        with _captured_statements() as statements:
            await additional_repo.add_entry(
                account_id,
                limit_name="requests_per_minute",
                metered_feature="api_calls",
                window="1m",
                used_percent=3.0,
            )
        _assert_relaxed_before(statements, "INSERT INTO additional_usage_history")


@pytest.mark.asyncio
async def test_configuration_writes_keep_full_commit_durability(db_setup) -> None:
    _require_postgres()
    async with SessionLocal() as session:
        with _captured_statements() as statements:
            await ApiKeysRepository(session).create(_make_api_key(f"key-{uuid4().hex[:8]}"))
        _assert_not_relaxed(statements)

        with _captured_statements() as statements:
            await AccountsRepository(session).upsert(_make_account(f"acc-{uuid4().hex[:8]}"))
        _assert_not_relaxed(statements)
