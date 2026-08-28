from __future__ import annotations

from datetime import datetime
from typing import cast

import pytest

from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.mappers import build_account_summaries
from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore

pytestmark = pytest.mark.unit

_DEFAULT_CHATGPT_ACCOUNT_ID = object()


def _account(
    account_id: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    chatgpt_account_id: str | None | object = _DEFAULT_CHATGPT_ACCOUNT_ID,
) -> Account:
    if chatgpt_account_id is _DEFAULT_CHATGPT_ACCOUNT_ID:
        resolved_chatgpt_account_id: str | None = f"workspace-{account_id}"
    else:
        resolved_chatgpt_account_id = cast("str | None", chatgpt_account_id)
    return Account(
        id=account_id,
        chatgpt_account_id=resolved_chatgpt_account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"",
        refresh_token_encrypted=b"",
        id_token_encrypted=b"",
        last_refresh=datetime(2025, 1, 1),
        status=status,
    )


def _summaries(accounts: list[Account], store: RateLimitResetCreditsStore):
    return build_account_summaries(
        accounts=accounts,
        primary_usage={},
        secondary_usage={},
        encryptor=TokenEncryptor(),
        include_auth=False,
        reset_credits_store=store,
    )


def test_account_summary_exposes_cached_reset_credits_fields() -> None:
    store = RateLimitResetCreditsStore()
    nearest = datetime(2026, 7, 10, 0, 0, 0)
    store_snapshot = RateLimitResetCreditsSnapshot(
        available_count=2,
        nearest_expires_at=nearest,
        credits=[],
    )
    # Bypass the async lock by writing the backing dict directly for a unit fixture.
    store._snapshots["acc_with_credits"] = store_snapshot  # type: ignore[attr-defined]

    [summary] = _summaries([_account("acc_with_credits")], store)

    assert summary.available_reset_credits == 2
    assert summary.reset_credit_nearest_expires_at == nearest


def test_account_summary_returns_zero_and_null_when_no_snapshot() -> None:
    store = RateLimitResetCreditsStore()

    [summary] = _summaries([_account("acc_no_cache")], store)

    assert summary.available_reset_credits == 0
    assert summary.reset_credit_nearest_expires_at is None


@pytest.mark.parametrize(
    "status",
    [AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED],
)
def test_account_summary_suppresses_cached_reset_credits_for_ineligible_status(status: AccountStatus) -> None:
    store = RateLimitResetCreditsStore()
    store._snapshots["acc_ineligible"] = RateLimitResetCreditsSnapshot(  # type: ignore[attr-defined]
        available_count=3,
        nearest_expires_at=datetime(2026, 6, 20, 0, 0, 0),
        credits=[],
    )

    [summary] = _summaries([_account("acc_ineligible", status=status)], store)

    assert summary.available_reset_credits == 0
    assert summary.reset_credit_nearest_expires_at is None


def test_account_summary_suppresses_cached_reset_credits_without_chatgpt_account_id() -> None:
    store = RateLimitResetCreditsStore()
    store._snapshots["acc_no_workspace"] = RateLimitResetCreditsSnapshot(  # type: ignore[attr-defined]
        available_count=3,
        nearest_expires_at=datetime(2026, 6, 20, 0, 0, 0),
        credits=[],
    )

    [summary] = _summaries([_account("acc_no_workspace", chatgpt_account_id=None)], store)

    assert summary.available_reset_credits == 0
    assert summary.reset_credit_nearest_expires_at is None


def test_account_summary_mixed_cache_state_across_accounts() -> None:
    store = RateLimitResetCreditsStore()
    store._snapshots["acc_has"] = RateLimitResetCreditsSnapshot(  # type: ignore[attr-defined]
        available_count=5,
        nearest_expires_at=datetime(2026, 6, 20, 0, 0, 0),
        credits=[],
    )

    summaries = _summaries([_account("acc_has"), _account("acc_missing")], store)
    by_id = {s.account_id: s for s in summaries}

    assert by_id["acc_has"].available_reset_credits == 5
    assert by_id["acc_has"].reset_credit_nearest_expires_at is not None
    assert by_id["acc_missing"].available_reset_credits == 0
    assert by_id["acc_missing"].reset_credit_nearest_expires_at is None


def test_account_summary_does_not_crash_when_store_is_empty() -> None:
    store = RateLimitResetCreditsStore()
    accounts = [_account(f"acc_{i}") for i in range(3)]

    summaries = _summaries(accounts, store)

    assert len(summaries) == 3
    assert all(s.available_reset_credits == 0 for s in summaries)
    assert all(s.reset_credit_nearest_expires_at is None for s in summaries)
