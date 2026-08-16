from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.db.account_identity_lock import lock_postgresql_account_identities
from app.modules.usage import repository as usage_repository_module
from app.modules.usage.repository import (
    LiveSnapshotOwnerIdentityRelockError,
    UsageRepository,
    UsageWindowWrite,
)

pytestmark = pytest.mark.unit


def _identity_result(account_id: str | None, chatgpt_account_id: str | None = None) -> MagicMock:
    result = MagicMock()
    if account_id is None:
        result.one_or_none.return_value = None
    else:
        result.one_or_none.return_value = MagicMock(
            id=account_id,
            chatgpt_account_id=chatgpt_account_id,
        )
    return result


def _postgresql_session(results: list[MagicMock]) -> MagicMock:
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.execute = AsyncMock(side_effect=results)
    session.add_all = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


async def _settle(session: MagicMock) -> str | None:
    return await UsageRepository(session).settle_live_account_snapshot(
        account_id="acc-selected",
        chatgpt_account_id="workspace-x",
        windows=[UsageWindowWrite(window="primary", used_percent=25.0)],
        should_skip=lambda _account_id: False,
    )


@pytest.mark.asyncio
async def test_postgresql_live_snapshot_same_identity_does_not_relock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _postgresql_session(
        [
            _identity_result("acc-selected", "workspace-x"),
            _identity_result("acc-selected", "workspace-x"),
        ]
    )
    identity_lock = AsyncMock()
    monkeypatch.setattr(usage_repository_module, "lock_postgresql_account_identities", identity_lock)
    monkeypatch.setattr(usage_repository_module, "relax_commit_durability", AsyncMock())

    resolved = await _settle(session)

    assert resolved == "acc-selected"
    identity_lock.assert_awaited_once_with(session, ("workspace-x",))
    session.rollback.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgresql_live_snapshot_relocks_once_for_current_owner_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _postgresql_session(
        [
            _identity_result("acc-selected", "workspace-y"),
            _identity_result("acc-selected", "workspace-y"),
            _identity_result("acc-selected", "workspace-y"),
        ]
    )
    identity_lock = AsyncMock()
    monkeypatch.setattr(usage_repository_module, "lock_postgresql_account_identities", identity_lock)
    monkeypatch.setattr(usage_repository_module, "relax_commit_durability", AsyncMock())

    resolved = await _settle(session)

    assert resolved == "acc-selected"
    assert identity_lock.await_args_list == [
        call(session, ("workspace-x",)),
        call(session, ("workspace-x", "workspace-y")),
    ]
    session.rollback.assert_awaited_once()
    session.add_all.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgresql_live_snapshot_second_owner_identity_change_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _postgresql_session(
        [
            _identity_result("acc-selected", "workspace-y"),
            _identity_result("acc-selected", "workspace-z"),
        ]
    )
    identity_lock = AsyncMock()
    monkeypatch.setattr(usage_repository_module, "lock_postgresql_account_identities", identity_lock)

    with pytest.raises(LiveSnapshotOwnerIdentityRelockError):
        await _settle(session)

    assert identity_lock.await_args_list == [
        call(session, ("workspace-x",)),
        call(session, ("workspace-x", "workspace-y")),
    ]
    assert session.rollback.await_count == 2
    session.add_all.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_postgresql_identity_lock_does_not_fabricate_none_key() -> None:
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.execute = AsyncMock()

    lock_keys = await lock_postgresql_account_identities(session, (None,))

    assert lock_keys == ()
    session.execute.assert_not_awaited()
