from __future__ import annotations

from datetime import datetime

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.mappers import build_account_summaries

pytestmark = pytest.mark.unit


def _make_account(*, routing_policy: str | None) -> Account:
    return Account(
        id="acc-1",
        chatgpt_account_id="chatgpt-acc-1",
        email="account@example.com",
        plan_type="plus",
        access_token_encrypted=b"",
        refresh_token_encrypted=b"",
        id_token_encrypted=b"",
        last_refresh=datetime(2025, 1, 1),
        status=AccountStatus.ACTIVE,
        routing_policy=routing_policy,
    )


def test_account_summary_normalizes_unknown_routing_policy_to_normal():
    summaries = build_account_summaries(
        accounts=[_make_account(routing_policy="legacy")],
        primary_usage={},
        secondary_usage={},
        encryptor=TokenEncryptor(),
        include_auth=False,
    )

    assert summaries[0].routing_policy == "normal"


def test_account_summary_preserves_known_routing_policy():
    summaries = build_account_summaries(
        accounts=[_make_account(routing_policy="preserve")],
        primary_usage={},
        secondary_usage={},
        encryptor=TokenEncryptor(),
        include_auth=False,
    )

    assert summaries[0].routing_policy == "preserve"
