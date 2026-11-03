from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.core.clients import usage_probe
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.mappers import build_account_summaries
from app.modules.proxy.load_balancer import RuntimeState, _state_from_account
from app.modules.usage.repository import UsageWindowWrite
from app.modules.usage.reset_recovery import reset_confirmed
from app.modules.usage.updater import UsageRepositoryPort, UsageUpdater

pytestmark = pytest.mark.unit


def _account() -> Account:
    return Account(
        id="synthetic",
        email="test@example.invalid",
        plan_type="plus",
        status=AccountStatus.RATE_LIMITED,
        blocked_at=int(time.time()) - 10,
        reset_at=int(time.time()) + 3600,
    )


def _entry(*, used: float = 100, reset: int | None = None, window: str = "secondary") -> UsageHistory:
    return UsageHistory(
        account_id="synthetic",
        window=window,
        used_percent=used,
        reset_at=reset or int(time.time()) + 3600,
        window_minutes=10080 if window == "secondary" else 300,
        recorded_at=utcnow(),
    )


@pytest.mark.parametrize(
    "case", ["unchanged_reset", "no_drop", "full_sibling", "missing_sibling", "older_baseline", "jitter"]
)
def test_reset_evidence_rejects_incomplete_or_unrelated_samples(case):
    account = _account()
    previous = _entry()
    assert previous.reset_at is not None
    before = {"secondary": previous}
    after = [
        UsageWindowWrite(window="secondary", used_percent=0, reset_at=previous.reset_at + 604800, window_minutes=10080)
    ]
    if case == "unchanged_reset":
        after[0] = UsageWindowWrite(
            window="secondary", used_percent=0, reset_at=previous.reset_at, window_minutes=10080
        )
    elif case == "jitter":
        after[0] = UsageWindowWrite(
            window="secondary", used_percent=0, reset_at=previous.reset_at + 1, window_minutes=10080
        )
    elif case == "no_drop":
        previous.used_percent = 0
    elif case in ("full_sibling", "missing_sibling"):
        before["primary"] = _entry(window="primary")
        if case == "full_sibling":
            after.append(
                UsageWindowWrite(window="primary", used_percent=100, reset_at=account.reset_at, window_minutes=300)
            )
    else:
        previous.recorded_at = utcnow() - timedelta(minutes=1)
    assert not reset_confirmed(account, before, after, operator_reset=case != "older_baseline")


def test_natural_reset_does_not_override_unrelated_retry_after():
    account = _account()
    before = _entry(reset=int(time.time()) + 7200)
    assert before.reset_at is not None
    after = [
        UsageWindowWrite(window="secondary", used_percent=0, reset_at=before.reset_at + 604800, window_minutes=10080)
    ]
    assert not reset_confirmed(account, {"secondary": before}, after, operator_reset=False)
    assert reset_confirmed(account, {"secondary": before}, after, operator_reset=True)


@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED])
async def test_wake_refuses_hard_blocked_accounts(status):
    account = _account()
    account.status = status
    usage = SimpleNamespace(latest_entry_for_account=AsyncMock(), add_account_snapshot=AsyncMock())
    updater = UsageUpdater(cast(UsageRepositoryPort, usage))
    assert not await updater.wake_and_refresh(account)
    usage.latest_entry_for_account.assert_not_awaited()


@pytest.mark.parametrize("later_error", [False, True])
def test_runtime_reset_projection_preserves_newer_errors_and_leases(later_error):
    account = _account()
    account.status, account.blocked_at, account.reset_at = AccountStatus.ACTIVE, None, None
    now = time.time()
    runtime = RuntimeState(
        blocked_at=now - 30,
        reset_at=now + 3600,
        cooldown_until=now + 3600,
        last_error_at=now + 1 if later_error else now - 30,
        error_count=1,
        inflight_streams=2,
        leased_tokens=123,
        probe_success_streak=1,
    )
    entry = _entry(used=0)
    state = _state_from_account(account=account, primary_entry=None, secondary_entry=entry, runtime=runtime)
    assert (state.cooldown_until is not None) == later_error
    assert state.error_count == 1 and state.last_error_at == runtime.last_error_at
    assert state.inflight_streams == 2 and state.leased_tokens == 123


def test_elapsed_cooldown_badge_uses_router_projection():
    account = _account()
    account.reset_at = int(time.time()) - 30
    account.blocked_at = int(time.time()) - 300
    primary, secondary = _entry(used=0, window="primary"), _entry(used=0)
    state = _state_from_account(
        account=account, primary_entry=primary, secondary_entry=secondary, runtime=RuntimeState()
    )
    summary = build_account_summaries(
        accounts=[account],
        primary_usage={account.id: primary},
        secondary_usage={account.id: secondary},
        encryptor=TokenEncryptor(),
        include_auth=False,
    )[0]
    assert state.status == AccountStatus.ACTIVE
    assert summary.status == state.status.value


async def test_probe_uses_resolved_route_and_closes_transport(monkeypatch):
    from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

    route = ResolvedUpstreamRoute(
        mode="account",
        pool_id="synthetic-pool",
        endpoint=ResolvedProxyEndpoint(
            id="synthetic-proxy",
            scheme="http",
            host="proxy.invalid",
            port=8080,
        ),
    )
    client = SimpleNamespace(request=AsyncMock(return_value=SimpleNamespace(status=200)), close=AsyncMock())
    monkeypatch.setattr(usage_probe, "create_codex_session", lambda: None)
    monkeypatch.setattr(usage_probe, "CodexClient", lambda _: client)
    assert (
        await usage_probe.send_usage_probe(access_token="synthetic-token", chatgpt_account_id="workspace", route=route)
        == 200
    )
    assert client.request.await_args.kwargs["route"] is route
    assert client.request.await_args.kwargs["headers"]["chatgpt-account-id"] == "workspace"
    assert client.request.await_args.kwargs["json"]["max_output_tokens"] == 1
    client.close.assert_awaited_once()
