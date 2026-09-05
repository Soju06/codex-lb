"""The balancer's selection-time clock reads come from its injected clock.

``LoadBalancer(clock=...)`` owns the epoch clock used by account selection.
The reauthentication and additional-quota eligibility helpers used to read
``time.time()`` themselves; they now take the balancer's sample so a virtual
clock steers every decision and the wall clock is never consulted. The tests
make ``time.time`` raise to prove the paths below never touch it.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.modules.proxy.account_eligibility import all_accounts_require_reauthentication
from app.modules.proxy.load_balancer import LoadBalancer
from tests.simulation.virtual_time import VirtualClock

pytestmark = pytest.mark.unit


def _forbid_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install after the fixtures are built: Fernet stamps ``time.time()`` when encrypting."""

    def wall_clock_read() -> float:
        raise AssertionError("time.time() must not be read on the injected-clock path")

    monkeypatch.setattr(time, "time", wall_clock_read)


def _jwt(*, expires_at: int) -> str:
    def encode(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode({'exp': expires_at})}."


def _account(account_id: str, *, encryptor: TokenEncryptor, expires_at: int, plan_type: str = "pro") -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"chatgpt-{account_id}",
        email=f"{account_id}@example.com",
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt(_jwt(expires_at=expires_at)),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 1, 1),
        status=AccountStatus.REAUTH_REQUIRED,
    )


def _additional_usage(account_id: str, *, used_percent: float, reset_at: int | None) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        account_id=account_id,
        quota_key="codex_spark",
        limit_name="codex-spark",
        metered_feature="codex_bengalfox",
        window="primary",
        used_percent=used_percent,
        reset_at=reset_at,
        recorded_at=datetime(2026, 1, 1),
    )


def test_all_accounts_require_reauthentication_uses_the_caller_clock_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    encryptor = TokenEncryptor()
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    expired = _account("expired", encryptor=encryptor, expires_at=int(clock.time()) - 1)
    still_valid = _account("valid", encryptor=encryptor, expires_at=int(clock.time()) + 3600)
    _forbid_wall_clock(monkeypatch)

    assert all_accounts_require_reauthentication([expired], encryptor, now=clock.time()) is True
    assert all_accounts_require_reauthentication([expired, still_valid], encryptor, now=clock.time()) is False
    assert all_accounts_require_reauthentication([], encryptor, now=clock.time()) is False
    clock.advance(3600.0)
    assert all_accounts_require_reauthentication([expired, still_valid], encryptor, now=clock.time()) is True


@pytest.mark.asyncio
async def test_additional_limit_filter_evaluates_quota_resets_on_the_injected_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryptor = TokenEncryptor()
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(cast(Any, lambda: None), encryptor=encryptor, clock=clock)
    exhausted = _account("exhausted", encryptor=encryptor, expires_at=int(clock.time()) + 3600)
    exhausted.status = AccountStatus.ACTIVE
    reset_at = int(clock.time()) + 600
    entries = {exhausted.id: _additional_usage(exhausted.id, used_percent=100.0, reset_at=reset_at)}
    _forbid_wall_clock(monkeypatch)

    async def latest_by_quota_key(quota_key: str, window: str, **_kwargs: object) -> dict[str, AdditionalUsageHistory]:
        assert quota_key == "codex_spark"
        return dict(entries) if window == "primary" else {}

    repos = SimpleNamespace(additional_usage=SimpleNamespace(latest_by_quota_key=latest_by_quota_key))

    before_reset = await balancer._filter_accounts_for_additional_limit(
        [exhausted],
        model="gpt-5.3-codex-spark",
        limit_name="codex-spark",
        explicit_limit=True,
        repos=cast(Any, repos),
    )
    clock.advance(600.0)
    at_reset = await balancer._filter_accounts_for_additional_limit(
        [exhausted],
        model="gpt-5.3-codex-spark",
        limit_name="codex-spark",
        explicit_limit=True,
        repos=cast(Any, repos),
    )

    assert before_reset.accounts == []
    assert before_reset.error_code == "quota_exhausted"
    assert [account.id for account in at_reset.accounts] == ["exhausted"]
    assert at_reset.error_code is None
