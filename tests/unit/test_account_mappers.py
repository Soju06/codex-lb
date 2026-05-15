from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.mappers import build_account_summaries

pytestmark = pytest.mark.unit


def _make_account(*, routing_policy: str | None, status: AccountStatus | None = AccountStatus.ACTIVE) -> Account:
    encryptor = TokenEncryptor()
    account = Account(
        id="acc-1",
        chatgpt_account_id="chatgpt-acc-1",
        email="account@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=status,
    )
    if routing_policy is not None:
        account.routing_policy = routing_policy
    return account


def test_account_summary_normalizes_unknown_routing_policy_to_normal() -> None:
    summaries = build_account_summaries(
        accounts=[_make_account(routing_policy="legacy")],
        primary_usage={},
        secondary_usage={},
        encryptor=TokenEncryptor(),
        include_auth=False,
    )

    assert summaries[0].routing_policy == "normal"


def test_account_summary_preserves_known_routing_policy() -> None:
    summaries = build_account_summaries(
        accounts=[_make_account(routing_policy="preserve")],
        primary_usage={},
        secondary_usage={},
        encryptor=TokenEncryptor(),
        include_auth=False,
    )

    assert summaries[0].routing_policy == "preserve"


def test_build_account_summaries_defaults_missing_status_to_active() -> None:
    summaries = build_account_summaries(
        accounts=[_make_account(routing_policy="normal", status=None)],
        primary_usage={},
        secondary_usage={},
        encryptor=TokenEncryptor(),
        include_auth=False,
    )

    assert summaries[0].status == AccountStatus.ACTIVE.value
