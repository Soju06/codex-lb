from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.modules.accounts.repository as repository_module
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.account_identity_lock import account_identity_lock_key, lock_postgresql_account_identities
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountIdentityRelockError, AccountsRepository


def _stub_account(account_id: str, email: str, chatgpt_id: str | None = None) -> Account:
    enc = TokenEncryptor()
    acc = Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=enc.encrypt("a"),
        refresh_token_encrypted=enc.encrypt("r"),
        id_token_encrypted=enc.encrypt("i"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    if chatgpt_id is not None:
        acc.chatgpt_account_id = chatgpt_id
    return acc


def _make_postgres_repo(monkeypatch: pytest.MonkeyPatch) -> tuple[AccountsRepository, dict[str, list[str]]]:
    """Build an AccountsRepository whose dialect reports postgresql and
    whose lock acquisitions and session writes are all stubbed.

    Returns the repo plus a dict of ordered lock-key recordings keyed by
    lock type, so callers can assert the exact lock sequence.
    """
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=None)

    repo = AccountsRepository(session)

    recorded: dict[str, list[str]] = {"upstream": [], "identity": [], "email": [], "order": []}

    async def fake_identity_lock(key: str) -> None:
        recorded["identity"].append(key)
        recorded["order"].append(f"identity:{key}")

    async def fake_email_lock(email: str) -> None:
        recorded["email"].append(email)
        recorded["order"].append(f"email:{email}")

    async def fake_upstream_identity_locks(account: Account, *, include_email: bool) -> frozenset[str]:
        del include_email
        if account.chatgpt_account_id:
            recorded["upstream"].append(account.chatgpt_account_id)
            recorded["order"].append(f"upstream:{account.chatgpt_account_id}")
            return frozenset((account.chatgpt_account_id,))
        return frozenset()

    async def fake_candidates_are_locked(
        account: Account,
        *,
        include_email: bool,
        locked_identities: frozenset[str],
    ) -> bool:
        del account
        del include_email
        del locked_identities
        return True

    async def fake_merge_by_email_enabled() -> bool:  # only used when merge_by_email is None
        return True

    async def fake_account_by_chatgpt_identity(
        _chatgpt_id: str,
        *,
        workspace_id: str | None,
        email: str | None,
    ):
        del workspace_id
        del email
        return None

    async def fake_single_account_by_email(_email: str):
        return None

    async def fake_next_available_account_id(account_id: str) -> str:
        return account_id

    monkeypatch.setattr(repo, "_dialect_name", lambda: "postgresql")
    monkeypatch.setattr(repo, "_acquire_postgresql_identity_lock", fake_identity_lock)
    monkeypatch.setattr(repo, "_acquire_postgresql_merge_lock", fake_email_lock)
    monkeypatch.setattr(repo, "_lock_postgresql_upsert_identity_candidates", fake_upstream_identity_locks)
    monkeypatch.setattr(repo, "_postgresql_upsert_identity_candidates_are_locked", fake_candidates_are_locked)
    monkeypatch.setattr(repo, "_merge_by_email_enabled", fake_merge_by_email_enabled)
    monkeypatch.setattr(repo, "_account_by_chatgpt_identity", fake_account_by_chatgpt_identity)
    monkeypatch.setattr(repo, "_account_by_slot_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(repo, "_single_account_by_email", fake_single_account_by_email)
    monkeypatch.setattr(repo, "_single_unknown_workspace_account_by_email", fake_single_account_by_email)
    monkeypatch.setattr(repo, "_next_available_account_id", fake_next_available_account_id)

    return repo, recorded


def _make_result(value: str | None = "acc") -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_postgresql_upstream_identity_locks_use_existing_namespace_in_sorted_order() -> None:
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.execute = AsyncMock()

    lock_keys = await lock_postgresql_account_identities(session, ("workspace-z", None, "workspace-a", "workspace-z"))

    expected = tuple(sorted((account_identity_lock_key("workspace-a"), account_identity_lock_key("workspace-z"))))
    assert lock_keys == expected
    assert session.execute.await_args_list[0].args[1] == {"timeout": "30000ms"}
    assert [call.args[1]["lock_key"] for call in session.execute.await_args_list[1:]] == list(expected)


@pytest.mark.asyncio
async def test_postgresql_upstream_identity_lock_failure_rolls_back_and_propagates() -> None:
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    lock_error = RuntimeError("injected lock timeout")
    session.execute = AsyncMock(side_effect=[MagicMock(), lock_error])
    session.rollback = AsyncMock()

    with pytest.raises(RuntimeError) as exc_info:
        await lock_postgresql_account_identities(session, ("workspace-timeout",))

    assert exc_info.value is lock_error
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_account_update_status_uses_sqlite_writer_section(monkeypatch):
    session = MagicMock()
    session.execute = AsyncMock(return_value=_make_result("acc"))
    session.scalar = AsyncMock(return_value=AccountStatus.ACTIVE)
    session.commit = AsyncMock()
    repo = AccountsRepository(session)
    order: list[str] = []

    @asynccontextmanager
    async def fake_writer_section():
        order.append("lock-enter")
        yield
        order.append("lock-exit")

    async def execute_with_order(*args, **kwargs):
        del args, kwargs
        order.append("execute")
        return _make_result("acc")

    async def scalar_with_order(*args, **kwargs):
        del args, kwargs
        order.append("scalar")
        return AccountStatus.ACTIVE

    async def commit_with_order():
        order.append("commit")

    monkeypatch.setattr(repository_module, "sqlite_writer_section", fake_writer_section)
    session.execute.side_effect = execute_with_order
    session.scalar.side_effect = scalar_with_order
    session.commit.side_effect = commit_with_order

    assert await repo.update_status("acc", AccountStatus.RATE_LIMITED) is True

    assert order == ["lock-enter", "scalar", "execute", "execute", "commit", "lock-exit"]


@pytest.mark.asyncio
async def test_account_rotate_tokens_uses_sqlite_writer_section(monkeypatch):
    session = MagicMock()
    session.execute = AsyncMock(return_value=_make_result("acc"))
    session.commit = AsyncMock()
    repo = AccountsRepository(session)
    order: list[str] = []

    @asynccontextmanager
    async def fake_writer_section():
        order.append("lock-enter")
        yield
        order.append("lock-exit")

    async def execute_with_order(*args, **kwargs):
        del args, kwargs
        order.append("execute")
        return _make_result("acc")

    async def commit_with_order():
        order.append("commit")

    monkeypatch.setattr(repository_module, "sqlite_writer_section", fake_writer_section)
    session.execute.side_effect = execute_with_order
    session.commit.side_effect = commit_with_order

    assert await repo.rotate_tokens(
        "acc",
        b"access",
        b"refresh",
        b"id",
        utcnow(),
        expected_refresh_token_encrypted=b"refresh",
    )

    assert order == ["lock-enter", "execute", "commit", "lock-exit"]


@pytest.mark.asyncio
async def test_upsert_takes_identity_lock_even_when_merge_by_email_enabled(monkeypatch):
    """Pin the fix for the codex P2 finding on PR #799.

    When merge_by_email=True AND merge_by_chatgpt_identity=True with a
    chatgpt_account_id set, two concurrent reauths for the same upstream
    identity but different email claims would otherwise take different
    email-scoped locks, both miss the canonical-row lookup, and both
    INSERT a duplicate row for that identity. The fix takes the
    identity-keyed advisory lock first, then the email-scoped one.
    """

    repo, recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_a", "a@example.com", chatgpt_id="chatgpt_xyz")

    await repo.upsert(account, merge_by_email=True, merge_by_chatgpt_identity=True)

    assert recorded["upstream"] == ["chatgpt_xyz"], (
        "upstream identity lock must be acquired even when merge_by_email is True"
    )
    assert recorded["identity"] == []
    assert recorded["email"] == ["a@example.com"], "email lock must still be acquired when merge_by_email is True"


@pytest.mark.asyncio
async def test_upsert_takes_identity_lock_when_merge_by_email_disabled(monkeypatch):
    """Existing path: merge_by_email=False + merge_by_chatgpt_identity
    keys lock by upstream identity (unchanged behavior).
    """

    repo, recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_b", "b@example.com", chatgpt_id="chatgpt_zzz")

    await repo.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=True)

    assert recorded["upstream"] == ["chatgpt_zzz"]
    assert recorded["identity"] == []
    assert recorded["email"] == []


@pytest.mark.asyncio
async def test_upsert_falls_back_to_id_lock_without_identity(monkeypatch):
    """When identity reconciliation is off and merge_by_email is off, the
    per-account fallback lock keyed by account.id still fires (so two
    concurrent inserts of the same id serialize).
    """

    repo, recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_c", "c@example.com", chatgpt_id=None)

    await repo.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=False)

    assert recorded["upstream"] == []
    assert recorded["identity"] == ["acc_c"]
    assert recorded["email"] == []


@pytest.mark.asyncio
async def test_upsert_email_only_when_identity_not_in_play(monkeypatch):
    """merge_by_email=True without identity reconciliation keeps the
    pre-existing email-only lock behavior.
    """

    repo, recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_d", "d@example.com", chatgpt_id="chatgpt_qqq")

    await repo.upsert(account, merge_by_email=True, merge_by_chatgpt_identity=False)

    assert recorded["upstream"] == ["chatgpt_qqq"]
    assert recorded["identity"] == []
    assert recorded["email"] == ["d@example.com"]
    assert recorded["order"] == ["upstream:chatgpt_qqq", "email:d@example.com"]


@pytest.mark.asyncio
async def test_ordinary_identity_upsert_uses_upstream_membership_lock(monkeypatch):
    repo, recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_e", "e@example.com", chatgpt_id="chatgpt_ordinary")

    await repo.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=False)

    assert recorded["upstream"] == ["chatgpt_ordinary"]
    assert recorded["order"] == ["upstream:chatgpt_ordinary"]


@pytest.mark.asyncio
async def test_account_slot_upsert_locks_upstream_before_slot_keys(monkeypatch):
    repo, recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_slot", "slot@example.com", chatgpt_id="chatgpt_slot")
    account.workspace_id = "workspace-slot"

    await repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)

    assert recorded["upstream"] == ["chatgpt_slot"]
    assert recorded["order"][0] == "upstream:chatgpt_slot"
    assert all(item.startswith("identity:") for item in recorded["order"][1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("slot_upsert", [False, True])
async def test_identity_candidate_revalidation_restarts_once_then_succeeds(monkeypatch, slot_upsert: bool):
    repo, _recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_retry", "retry@example.com", chatgpt_id="chatgpt_retry")
    candidates_are_locked = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(repo, "_postgresql_upsert_identity_candidates_are_locked", candidates_are_locked)

    if slot_upsert:
        saved = await repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)
    else:
        saved = await repo.upsert(account, merge_by_email=False)

    assert saved is account
    assert candidates_are_locked.await_count == 2
    assert cast(Any, repo.session.rollback).await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("slot_upsert", [False, True])
async def test_identity_candidate_revalidation_raises_typed_error_after_second_change(
    monkeypatch,
    slot_upsert: bool,
):
    repo, _recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_terminal", "terminal@example.com", chatgpt_id="chatgpt_terminal")
    monkeypatch.setattr(
        repo,
        "_postgresql_upsert_identity_candidates_are_locked",
        AsyncMock(side_effect=[False, False]),
    )

    with pytest.raises(AccountIdentityRelockError):
        if slot_upsert:
            await repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)
        else:
            await repo.upsert(account, merge_by_email=False)

    assert cast(Any, repo.session.rollback).await_count == 2


@pytest.mark.asyncio
async def test_local_identity_membership_relocks_after_observed_identity_changes(monkeypatch):
    session = MagicMock()
    changed = _stub_account("acc_relock", "relock@example.com", chatgpt_id="chatgpt_changed")
    session.scalar = AsyncMock(side_effect=["chatgpt_old", changed, "chatgpt_changed", changed])
    session.rollback = AsyncMock()
    repo = AccountsRepository(session)
    identity_locks = AsyncMock()
    monkeypatch.setattr(repository_module, "lock_postgresql_account_identities", identity_locks)

    locked = await repo._lock_postgresql_account_identity_membership("acc_relock", "chatgpt_incoming")

    assert locked is changed
    assert [call.args[1] for call in identity_locks.await_args_list] == [
        ("chatgpt_old", "chatgpt_incoming"),
        ("chatgpt_changed", "chatgpt_incoming"),
    ]
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_identity_membership_raises_typed_error_after_second_change(monkeypatch):
    session = MagicMock()
    changed_once = _stub_account("acc_relock", "relock@example.com", chatgpt_id="chatgpt_changed")
    changed_twice = _stub_account("acc_relock", "relock@example.com", chatgpt_id="chatgpt_changed_again")
    session.scalar = AsyncMock(side_effect=["chatgpt_old", changed_once, "chatgpt_changed", changed_twice])
    session.rollback = AsyncMock()
    repo = AccountsRepository(session)
    monkeypatch.setattr(repository_module, "lock_postgresql_account_identities", AsyncMock())

    with pytest.raises(AccountIdentityRelockError):
        await repo._lock_postgresql_account_identity_membership("acc_relock", "chatgpt_incoming")

    assert session.rollback.await_count == 2


@pytest.mark.asyncio
async def test_local_identity_writers_lock_old_and_incoming_membership(monkeypatch):
    repo, _recorded = _make_postgres_repo(monkeypatch)
    existing = _stub_account("acc_writer", "writer@example.com", chatgpt_id="chatgpt_old")
    membership_locks: list[tuple[str, str | None]] = []
    cast(Any, repo.session.execute).return_value = _make_result("acc_writer")

    async def fake_membership_lock(account_id: str, incoming: str | None) -> Account:
        membership_locks.append((account_id, incoming))
        return existing

    monkeypatch.setattr(repo, "_lock_postgresql_account_identity_membership", fake_membership_lock)
    monkeypatch.setattr(repo, "_apply_account_replacement", AsyncMock())
    monkeypatch.setattr(repository_module, "lock_fold_state", AsyncMock())
    monkeypatch.setattr(repository_module, "mirror_account_soft_delete_into_time_rollups", AsyncMock())

    await repo.replace_reauthorized(
        existing.id,
        _stub_account("incoming", existing.email, chatgpt_id="chatgpt_new"),
    )
    assert await repo.rotate_tokens(
        existing.id,
        b"access",
        b"refresh",
        b"id",
        utcnow(),
        expected_refresh_token_encrypted=b"expected",
        chatgpt_account_id="chatgpt_new",
    )
    assert await repo.update_account_metadata(existing.id, chatgpt_account_id="chatgpt_new")
    assert await repo.delete(existing.id)

    assert membership_locks == [
        (existing.id, "chatgpt_new"),
        (existing.id, "chatgpt_new"),
        (existing.id, "chatgpt_new"),
        (existing.id, None),
    ]
