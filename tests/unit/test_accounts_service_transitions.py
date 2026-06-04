from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.db.models import AccountStatus
from app.modules.accounts.service import AccountsService, AccountStateTransitionError

pytestmark = pytest.mark.unit

_ACCOUNT_ID = "acc_state_transition"


def _account(
    status: AccountStatus,
    *,
    deactivation_reason: str | None = None,
    reset_at: int | None = None,
    blocked_at: int | None = None,
) -> Any:
    return SimpleNamespace(
        status=status,
        deactivation_reason=deactivation_reason,
        reset_at=reset_at,
        blocked_at=blocked_at,
    )


@pytest.mark.asyncio
async def test_pause_account_uses_conditional_status_update() -> None:
    account = _account(AccountStatus.ACTIVE)
    repo = AsyncMock()
    repo.get_by_id.return_value = account
    repo.update_status_if_current.return_value = True
    service = AccountsService(repo=repo)

    result = await service.pause_account(_ACCOUNT_ID)

    assert result is True
    repo.update_status_if_current.assert_awaited_once_with(
        _ACCOUNT_ID,
        AccountStatus.PAUSED,
        None,
        None,
        blocked_at=None,
        expected_status=AccountStatus.ACTIVE,
        expected_deactivation_reason=None,
        expected_reset_at=None,
        expected_blocked_at=None,
    )
    repo.update_status.assert_not_called()


@pytest.mark.asyncio
async def test_pause_account_raises_when_conditional_update_misses() -> None:
    repo = AsyncMock()
    repo.get_by_id.return_value = _account(AccountStatus.ACTIVE)
    repo.update_status_if_current.return_value = False
    service = AccountsService(repo=repo)

    with pytest.raises(AccountStateTransitionError, match="state changed"):
        await service.pause_account(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_reactivate_account_rejects_reauth_required_account() -> None:
    repo = AsyncMock()
    repo.get_by_id.return_value = _account(
        AccountStatus.REAUTH_REQUIRED,
        deactivation_reason="Authentication token invalidated - re-login required",
    )
    service = AccountsService(repo=repo)

    with pytest.raises(AccountStateTransitionError, match="requires re-authentication"):
        await service.reactivate_account(_ACCOUNT_ID)

    repo.update_status_if_current.assert_not_called()
