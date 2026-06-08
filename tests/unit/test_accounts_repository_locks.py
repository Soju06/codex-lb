from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository


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
    session.add = MagicMock()
    session.get = AsyncMock(return_value=None)

    repo = AccountsRepository(session)

    recorded: dict[str, list[str]] = {"identity": [], "email": []}

    async def fake_identity_lock(key: str) -> None:
        recorded["identity"].append(key)

    async def fake_email_lock(email: str) -> None:
        recorded["email"].append(email)

    async def fake_merge_by_email_enabled() -> bool:  # only used when merge_by_email is None
        return True

    async def fake_account_by_chatgpt_identity(_chatgpt_id: str, *, workspace_id: str | None):
        del workspace_id
        return None

    async def fake_single_account_by_email(_email: str):
        return None

    async def fake_next_available_account_id(account_id: str) -> str:
        return account_id

    monkeypatch.setattr(repo, "_dialect_name", lambda: "postgresql")
    monkeypatch.setattr(repo, "_acquire_postgresql_identity_lock", fake_identity_lock)
    monkeypatch.setattr(repo, "_acquire_postgresql_merge_lock", fake_email_lock)
    monkeypatch.setattr(repo, "_merge_by_email_enabled", fake_merge_by_email_enabled)
    monkeypatch.setattr(repo, "_account_by_chatgpt_identity", fake_account_by_chatgpt_identity)
    monkeypatch.setattr(repo, "_single_account_by_email", fake_single_account_by_email)
    monkeypatch.setattr(repo, "_next_available_account_id", fake_next_available_account_id)

    return repo, recorded


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

    assert recorded["identity"] == ["chatgpt:chatgpt_xyz"], (
        "identity lock must be acquired even when merge_by_email is True"
    )
    assert recorded["email"] == ["a@example.com"], "email lock must still be acquired when merge_by_email is True"


@pytest.mark.asyncio
async def test_upsert_takes_identity_lock_when_merge_by_email_disabled(monkeypatch):
    """Existing path: merge_by_email=False + merge_by_chatgpt_identity
    keys lock by upstream identity (unchanged behavior).
    """

    repo, recorded = _make_postgres_repo(monkeypatch)
    account = _stub_account("acc_b", "b@example.com", chatgpt_id="chatgpt_zzz")

    await repo.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=True)

    assert recorded["identity"] == ["chatgpt:chatgpt_zzz"]
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

    assert recorded["identity"] == [], "no identity lock when merge_by_chatgpt_identity is False"
    assert recorded["email"] == ["d@example.com"]
