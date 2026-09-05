from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.load_balancer as load_balancer_module
from app.core.balancer import ERROR_BACKOFF_THRESHOLD, AccountState, evaluate_routing_pool, select_account
from app.core.usage.account_limits import AccountUsageLimitState
from app.db.models import Account, AccountStatus, StickySession, StickySessionKind, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.account_cache import AccountSelectionCache
from app.modules.proxy.load_balancer import AccountConcurrencyCaps, AccountSelection, LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.selection_errors import selection_failure_response
from app.modules.proxy.sticky_repository import StickyOwnerLookup, StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.authorization import OwnerAuthorizationKind
from app.modules.usage.mappers import usage_history_to_window_row
from app.modules.usage.repository import AccountUsageLimitSnapshot, AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit

_CONCURRENCY_CAPS = AccountConcurrencyCaps(response_create_limit=1, stream_limit=1)


@pytest.fixture(autouse=True)
def _isolate_runtime_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=False))
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: None)
    monkeypatch.setattr(load_balancer_module, "set_degraded", lambda _reason: None)


@pytest.fixture
def selection_cache() -> AccountSelectionCache:
    return AccountSelectionCache(ttl_seconds=60)


def _account(account_id: str, *, security_work_authorized: bool = False) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime.now(UTC),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        security_work_authorized=security_work_authorized,
    )


def _usage_row(
    row_id: int,
    account_id: str,
    *,
    window: str,
    used_percent: float,
) -> UsageHistory:
    now = datetime.now(UTC)
    window_minutes = {"primary": 300, "secondary": 10_080, "monthly": 43_200}[window]
    return UsageHistory(
        id=row_id,
        account_id=account_id,
        recorded_at=now,
        window=window,
        used_percent=used_percent,
        reset_at=int(now.timestamp()) + window_minutes * 60,
        window_minutes=window_minutes,
    )


class _AccountsRepository:
    def __init__(self, accounts: list[Account]) -> None:
        self.accounts = accounts
        self.list_calls = 0

    async def list_accounts(self) -> list[Account]:
        self.list_calls += 1
        return list(self.accounts)

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
        **_expected: Any,
    ) -> bool:
        account = next((candidate for candidate in self.accounts if candidate.id == account_id), None)
        if account is None:
            return False
        account.status = status
        account.deactivation_reason = deactivation_reason
        account.reset_at = reset_at
        account.blocked_at = blocked_at
        return True


class _UsageRepository:
    def __init__(
        self,
        *,
        accounts: list[Account],
        primary: dict[str, UsageHistory] | None = None,
        secondary: dict[str, UsageHistory] | None = None,
        monthly: dict[str, UsageHistory] | None = None,
    ) -> None:
        self.accounts = accounts
        self.rows = {
            "primary": primary or {},
            "secondary": secondary or {},
            "monthly": monthly or {},
        }
        self.calls = {"primary": 0, "secondary": 0, "monthly": 0}
        self.snapshot_calls = 0

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        del account_ids
        key = window or "primary"
        self.calls[key] += 1
        return dict(self.rows[key])

    async def account_usage_limit_snapshot(self, account_id: str) -> AccountUsageLimitSnapshot | None:
        self.snapshot_calls += 1
        account = next((candidate for candidate in self.accounts if candidate.id == account_id), None)
        if account is None:
            return None
        return AccountUsageLimitSnapshot(
            status=account.status,
            enabled=bool(account.usage_limit_enabled),
            limit_percent=account.usage_limit_percent,
            plan_type=account.plan_type,
            primary=(
                usage_history_to_window_row(self.rows["primary"][account_id])
                if account_id in self.rows["primary"]
                else None
            ),
            secondary=(
                usage_history_to_window_row(self.rows["secondary"][account_id])
                if account_id in self.rows["secondary"]
                else None
            ),
            monthly=(
                usage_history_to_window_row(self.rows["monthly"][account_id])
                if account_id in self.rows["monthly"]
                else None
            ),
        )


class _StickySessionsRepository:
    def __init__(self) -> None:
        self.account_id: str | None = None
        self.get_calls = 0

    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
        del args, kwargs
        self.get_calls += 1
        return self.account_id

    async def get_account_id_and_abandonment(self, *args: Any, **kwargs: Any) -> StickyOwnerLookup:
        del args, kwargs
        self.get_calls += 1
        return StickyOwnerLookup(account_id=self.account_id, continuity_abandoned=False)

    async def upsert(
        self,
        key: str,
        account_id: str,
        *,
        kind: StickySessionKind,
    ) -> StickySession:
        self.account_id = account_id
        return StickySession(key=key, account_id=account_id, kind=kind)

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        self.account_id = None
        return True


@asynccontextmanager
async def _repositories(
    accounts: _AccountsRepository,
    usage: _UsageRepository,
    sticky_sessions: _StickySessionsRepository,
) -> AsyncIterator[ProxyRepositories]:
    yield ProxyRepositories(
        accounts=cast(AccountsRepository, accounts),
        usage=cast(UsageRepository, usage),
        request_logs=cast(RequestLogsRepository, object()),
        sticky_sessions=cast(StickySessionsRepository, sticky_sessions),
        api_keys=cast(ApiKeysRepository, object()),
        additional_usage=cast(AdditionalUsageRepository, object()),
    )


def _balancer(
    accounts: list[Account],
    cache: AccountSelectionCache,
    *,
    primary: dict[str, UsageHistory] | None = None,
    secondary: dict[str, UsageHistory] | None = None,
    monthly: dict[str, UsageHistory] | None = None,
) -> tuple[LoadBalancer, _AccountsRepository, _UsageRepository, _StickySessionsRepository]:
    accounts_repo = _AccountsRepository(accounts)
    usage_repo = _UsageRepository(accounts=accounts, primary=primary, secondary=secondary, monthly=monthly)
    sticky_repo = _StickySessionsRepository()
    balancer = LoadBalancer(lambda: _repositories(accounts_repo, usage_repo, sticky_repo))
    balancer._selection_inputs_cache = cache
    return balancer, accounts_repo, usage_repo, sticky_repo


async def _select_with_lease(balancer: LoadBalancer, *, sticky: bool) -> AccountSelection:
    return await balancer.select_account(
        "contract-session" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        estimated_lease_tokens=42.0,
        concurrency_caps=_CONCURRENCY_CAPS,
    )


@pytest.mark.asyncio
async def test_public_selection_returns_a_detached_success(selection_cache: AccountSelectionCache) -> None:
    persisted = _account("contract-success")
    balancer, _, _, _ = _balancer([persisted], selection_cache)

    selection = await balancer.select_account(routing_strategy="usage_weighted")

    assert selection.account is not None
    assert selection.account.id == persisted.id
    assert selection.account is not persisted
    assert selection.error_message is None
    assert selection.error_code is None
    assert selection.lease is None

    selection.account.email = "mutated@example.com"
    assert persisted.email == "contract-success@example.com"


@pytest.mark.asyncio
async def test_public_selection_with_default_fair_share_kwargs_matches_default_behavior(
    selection_cache: AccountSelectionCache,
) -> None:
    persisted = _account("contract-fair-share-default")
    balancer, _, _, _ = _balancer([persisted], selection_cache)

    implicit = await balancer.select_account(routing_strategy="usage_weighted")
    explicit = await balancer.select_account(
        routing_strategy="usage_weighted",
        api_key_id=None,
        api_key_stream_fair_share_threshold_pct=0,
    )

    assert implicit.account is not None
    assert explicit.account is not None
    assert implicit.account.id == explicit.account.id == persisted.id
    assert (explicit.error_message, explicit.error_code) == (implicit.error_message, implicit.error_code)
    assert explicit.error_message is None
    assert explicit.error_code is None
    assert explicit.lease is None


@pytest.mark.asyncio
async def test_public_selection_surfaces_fair_share_denial_shape(
    selection_cache: AccountSelectionCache,
) -> None:
    account = _account("contract-fair-share-denied")
    balancer, _, _, _ = _balancer([account], selection_cache)
    # Pool capacity is 1 (one account, stream cap 1). A pre-loaded runtime puts
    # the requester at 2 in-flight streams: congested at threshold 100
    # (2 * 100 >= 1 * 100) and over its share of max(2, 1 // 1) = 2.
    balancer._runtime[account.id] = load_balancer_module.RuntimeState(
        inflight_streams=2,
        stream_key_inflight={"k1": 2},
    )

    selection = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=_CONCURRENCY_CAPS,
        api_key_id="k1",
        api_key_stream_fair_share_threshold_pct=100,
    )

    assert selection.account is None
    assert selection.lease is None
    assert selection.error_code == "api_key_stream_fair_share"
    assert selection.error_message is not None
    assert "fair share" in selection.error_message


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
@pytest.mark.parametrize(
    ("candidate_status", "expected_error_code"),
    [
        (AccountStatus.ACTIVE, "api_key_stream_fair_share"),
        (AccountStatus.PAUSED, "account_usage_limit_reached"),
    ],
    ids=["routable", "paused"],
)
async def test_public_fair_share_uses_only_routable_usage_eligible_capacity(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
    candidate_status: AccountStatus,
    expected_error_code: str,
) -> None:
    eligible = _account(f"contract-fair-share-eligible-{sticky}-{candidate_status.value}")
    limited = _account(f"contract-fair-share-limited-{sticky}")
    limited.usage_limit_enabled = True
    limited.usage_limit_percent = 10.0
    balancer, _, _, _ = _balancer(
        [eligible, limited],
        selection_cache,
        primary={
            eligible.id: _usage_row(20, eligible.id, window="primary", used_percent=5.0),
            limited.id: _usage_row(21, limited.id, window="primary", used_percent=10.0),
        },
    )
    selection_inputs = await balancer._load_selection_inputs(model=None)
    # Inject PAUSED after fresh-load prefiltering to exercise selection-path eligibility.
    next(account for account in selection_inputs.accounts if account.id == eligible.id).status = candidate_status
    monkeypatch.setattr(balancer, "_load_selection_inputs", AsyncMock(return_value=selection_inputs))
    balancer._runtime[eligible.id] = load_balancer_module.RuntimeState(
        inflight_streams=3,
        stream_key_inflight={"requester": 2, "other": 1},
    )

    selection = await balancer.select_account(
        "contract-fair-share-sticky" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=AccountConcurrencyCaps(response_create_limit=4, stream_limit=4),
        api_key_id="requester",
        api_key_stream_fair_share_threshold_pct=50,
    )

    assert selection.account is None
    assert selection.error_code == expected_error_code


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_fair_share_counts_backoff_candidates_under_pool_wide_backoff(
    selection_cache: AccountSelectionCache,
    sticky: bool,
) -> None:
    nearer_backoff = _account(f"contract-fair-share-backoff-nearer-{sticky}")
    farther_backoff = _account(f"contract-fair-share-backoff-farther-{sticky}")
    balancer, _, _, _ = _balancer([nearer_backoff, farther_backoff], selection_cache)
    now = datetime.now(UTC).timestamp()
    # Both accounts are in transient error backoff, so no candidate is
    # routable right now, yet selection can still admit through controlled
    # backoff fallback. Pool capacity is 8 (2 accounts x stream cap 4); the
    # pool is congested at threshold 50 (7 x 100 >= 8 x 50) and the requester
    # holds 4 streams against a fair share of max(2, 8 // 2) = 4, so the new
    # stream must be denied by fair share instead of slipping through the
    # backoff fallback.
    balancer._runtime[nearer_backoff.id] = load_balancer_module.RuntimeState(
        error_count=3,
        last_error_at=now - 10,
        inflight_streams=4,
        stream_key_inflight={"other": 3, "requester": 1},
    )
    balancer._runtime[farther_backoff.id] = load_balancer_module.RuntimeState(
        error_count=3,
        last_error_at=now,
        inflight_streams=3,
        stream_key_inflight={"requester": 3},
    )

    selection = await balancer.select_account(
        "contract-fair-share-backoff-sticky" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=AccountConcurrencyCaps(response_create_limit=4, stream_limit=4),
        api_key_id="requester",
        api_key_stream_fair_share_threshold_pct=50,
    )

    assert selection.account is None
    assert selection.error_code == "api_key_stream_fair_share"


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_fair_share_ignores_usage_limited_counters_when_admitting(
    selection_cache: AccountSelectionCache,
    sticky: bool,
) -> None:
    eligible = _account(f"contract-fair-share-admit-eligible-{sticky}")
    limited = _account(f"contract-fair-share-admit-limited-{sticky}")
    limited.usage_limit_enabled = True
    limited.usage_limit_percent = 10.0
    balancer, _, _, _ = _balancer(
        [eligible, limited],
        selection_cache,
        primary={
            eligible.id: _usage_row(30, eligible.id, window="primary", used_percent=5.0),
            limited.id: _usage_row(31, limited.id, window="primary", used_percent=10.0),
        },
    )
    balancer._runtime[limited.id] = load_balancer_module.RuntimeState(
        inflight_streams=6,
        stream_key_inflight={"requester": 3, "other": 3},
    )

    selection = await balancer.select_account(
        "contract-fair-share-admit-sticky" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=AccountConcurrencyCaps(response_create_limit=3, stream_limit=3),
        api_key_id="requester",
        api_key_stream_fair_share_threshold_pct=100,
    )

    assert selection.account is not None
    assert selection.account.id == eligible.id
    assert selection.error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_fair_share_all_usage_limited_keeps_policy_error(
    selection_cache: AccountSelectionCache,
    sticky: bool,
) -> None:
    limited = _account(f"contract-fair-share-all-limited-{sticky}")
    limited.usage_limit_enabled = True
    limited.usage_limit_percent = 10.0
    balancer, _, _, _ = _balancer(
        [limited],
        selection_cache,
        primary={limited.id: _usage_row(40, limited.id, window="primary", used_percent=10.0)},
    )
    balancer._runtime[limited.id] = load_balancer_module.RuntimeState(
        inflight_streams=3,
        stream_key_inflight={"requester": 2, "other": 1},
    )

    selection = await balancer.select_account(
        "contract-all-limited-sticky" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=AccountConcurrencyCaps(response_create_limit=3, stream_limit=3),
        api_key_id="requester",
        api_key_stream_fair_share_threshold_pct=100,
    )

    assert selection.account is None
    assert selection.error_code == "account_usage_limit_reached"


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
@pytest.mark.parametrize(
    ("candidate_status", "candidate_in_backoff", "expected_error_code"),
    [
        (AccountStatus.ACTIVE, False, "account_stream_cap"),
        (AccountStatus.PAUSED, False, "account_usage_limit_reached"),
        (AccountStatus.ACTIVE, True, "account_stream_cap"),
    ],
    ids=["routable-capped", "paused-capped", "backoff-capped"],
)
async def test_public_account_caps_consider_only_routable_usage_eligible_peers(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
    candidate_status: AccountStatus,
    candidate_in_backoff: bool,
    expected_error_code: str,
) -> None:
    candidate = _account(f"contract-cap-candidate-{sticky}-{candidate_status.value}")
    limited = _account(f"contract-cap-limited-{sticky}-{candidate_status.value}")
    limited.usage_limit_enabled = True
    limited.usage_limit_percent = 10.0
    balancer, _, _, _ = _balancer(
        [candidate, limited],
        selection_cache,
        primary={
            candidate.id: _usage_row(43, candidate.id, window="primary", used_percent=5.0),
            limited.id: _usage_row(44, limited.id, window="primary", used_percent=10.0),
        },
    )
    selection_inputs = await balancer._load_selection_inputs(model=None)
    cached_candidate = next(account for account in selection_inputs.accounts if account.id == candidate.id)
    cached_candidate.status = candidate_status
    monkeypatch.setattr(balancer, "_load_selection_inputs", AsyncMock(return_value=selection_inputs))
    balancer._runtime[candidate.id] = load_balancer_module.RuntimeState(
        inflight_streams=1,
        error_count=3 if candidate_in_backoff else 0,
        last_error_at=datetime.now(UTC).timestamp() if candidate_in_backoff else None,
    )

    selection = await balancer.select_account(
        "contract-cap-sticky" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=_CONCURRENCY_CAPS,
    )

    assert selection.account is None
    assert selection.error_code == expected_error_code
    assert cached_candidate.status is candidate_status
    assert (
        next(account for account in selection_inputs.accounts if account.id == limited.id).status
        is AccountStatus.ACTIVE
    )


@pytest.mark.asyncio
async def test_public_unbound_backoff_fallback_respects_stream_cap(
    selection_cache: AccountSelectionCache,
) -> None:
    accounts = [_account(f"contract-backoff-capped-{index}") for index in range(2)]
    balancer, _, _, _ = _balancer(accounts, selection_cache)
    now = datetime.now(UTC).timestamp()
    for account in accounts:
        balancer._runtime[account.id] = load_balancer_module.RuntimeState(
            error_count=3,
            last_error_at=now,
            inflight_streams=1,
        )

    selection = await balancer.select_account(
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=_CONCURRENCY_CAPS,
    )

    assert selection.account is None
    assert selection.lease is None
    assert selection.error_code == "account_stream_cap"
    assert {runtime.inflight_streams for runtime in balancer._runtime.values()} == {1}


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_account_caps_retain_under_cap_backoff_fallback(
    selection_cache: AccountSelectionCache,
    sticky: bool,
) -> None:
    capped = _account(f"contract-cap-healthy-{sticky}")
    nearer_backoff = _account(f"contract-cap-nearer-backoff-{sticky}")
    farther_backoff = _account(f"contract-cap-farther-backoff-{sticky}")
    balancer, _, _, _ = _balancer([capped, nearer_backoff, farther_backoff], selection_cache)
    now = datetime.now(UTC).timestamp()
    balancer._runtime[capped.id] = load_balancer_module.RuntimeState(inflight_streams=1)
    balancer._runtime[nearer_backoff.id] = load_balancer_module.RuntimeState(
        error_count=3,
        last_error_at=now - 10,
    )
    balancer._runtime[farther_backoff.id] = load_balancer_module.RuntimeState(
        error_count=3,
        last_error_at=now - 5,
    )

    selection = await balancer.select_account(
        "contract-cap-backoff-sticky" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        concurrency_caps=_CONCURRENCY_CAPS,
    )

    assert selection.account is not None
    assert selection.account.id == nearer_backoff.id
    assert selection.error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("other_block", ["usage_limit", "paused"])
async def test_public_backoff_fallback_cannot_reintroduce_usage_limited_accounts(
    selection_cache: AccountSelectionCache,
    other_block: str,
) -> None:
    limited = _account(f"contract-backoff-limited-{other_block}")
    limited.usage_limit_enabled = True
    limited.usage_limit_percent = 10.0
    other = _account(f"contract-backoff-other-{other_block}")
    primary = {
        limited.id: _usage_row(41, limited.id, window="primary", used_percent=10.0),
    }
    if other_block == "usage_limit":
        other.usage_limit_enabled = True
        other.usage_limit_percent = 10.0
        primary[other.id] = _usage_row(42, other.id, window="primary", used_percent=10.0)
    else:
        other.status = AccountStatus.PAUSED

    balancer, _, _, _ = _balancer([limited, other], selection_cache, primary=primary)
    now = datetime.now(UTC).timestamp()
    balancer._runtime[limited.id] = load_balancer_module.RuntimeState(error_count=3, last_error_at=now)
    if other_block == "usage_limit":
        balancer._runtime[other.id] = load_balancer_module.RuntimeState(error_count=3, last_error_at=now)

    selection = await balancer.select_account(routing_strategy="usage_weighted")

    assert selection.account is None
    assert selection.error_code == "account_usage_limit_reached"


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
@pytest.mark.parametrize(
    "upstream_status",
    [AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED],
    ids=["rate-limited", "quota-exceeded"],
)
async def test_public_exhaustion_envelope_wins_when_account_also_reaches_local_cap(
    selection_cache: AccountSelectionCache,
    sticky: bool,
    upstream_status: AccountStatus,
) -> None:
    exhausted = _account(f"contract-upstream-and-local-{upstream_status}-{sticky}")
    exhausted.status = upstream_status
    exhausted.usage_limit_enabled = True
    exhausted.usage_limit_percent = 100.0
    usage = _usage_row(50, exhausted.id, window="primary", used_percent=100.0)
    exhausted.reset_at = usage.reset_at
    balancer, _, _, _ = _balancer(
        [exhausted],
        selection_cache,
        primary={exhausted.id: usage},
    )

    selection = await balancer.select_account(
        "contract-upstream-local-sticky" if sticky else None,
        sticky_kind=StickySessionKind.PROMPT_CACHE if sticky else None,
        sticky_max_age_seconds=600 if sticky else None,
        routing_strategy="usage_weighted",
    )
    status_code, payload = selection_failure_response(selection)

    assert selection.error_code == "usage_limit_reached"
    assert selection.resets_at == usage.reset_at
    assert status_code == 429
    assert payload["error"] == {
        "message": selection.error_message,
        "type": "usage_limit_reached",
        "code": "usage_limit_reached",
        "resets_at": usage.reset_at,
    }


@pytest.mark.asyncio
async def test_public_opportunistic_selection_preserves_local_usage_limit_code(
    selection_cache: AccountSelectionCache,
) -> None:
    limited = _account("contract-opportunistic-local-limit")
    limited.usage_limit_enabled = True
    limited.usage_limit_percent = 10.0
    balancer, _, _, _ = _balancer(
        [limited],
        selection_cache,
        primary={limited.id: _usage_row(60, limited.id, window="primary", used_percent=10.0)},
    )

    selection = await balancer.check_opportunistic_admission(
        model=None,
        account_ids=None,
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert selection.account is None
    assert selection.error_code == "account_usage_limit_reached"


@pytest.mark.asyncio
async def test_public_opportunistic_mixed_policy_pool_preserves_local_usage_limit_code(
    selection_cache: AccountSelectionCache,
) -> None:
    limited = _account("contract-opportunistic-mixed-limited")
    limited.routing_policy = "burn_first"
    limited.usage_limit_enabled = True
    limited.usage_limit_percent = 10.0
    preserve = _account("contract-opportunistic-mixed-preserve")
    preserve.routing_policy = "preserve"
    balancer, _, _, _ = _balancer(
        [limited, preserve],
        selection_cache,
        primary={limited.id: _usage_row(61, limited.id, window="primary", used_percent=10.0)},
        secondary={limited.id: _usage_row(62, limited.id, window="secondary", used_percent=10.0)},
    )

    selection = await balancer.check_opportunistic_admission(
        model=None,
        account_ids=None,
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert selection.account is None
    assert selection.error_code == "account_usage_limit_reached"


@pytest.mark.asyncio
async def test_public_opportunistic_limit_error_requires_causal_block(
    selection_cache: AccountSelectionCache,
) -> None:
    limited_preserve = _account("contract-opportunistic-noncausal-limited")
    limited_preserve.routing_policy = "preserve"
    limited_preserve.usage_limit_enabled = True
    limited_preserve.usage_limit_percent = 95.0
    normal = _account("contract-opportunistic-noncausal-normal")
    balancer, _, _, _ = _balancer(
        [limited_preserve, normal],
        selection_cache,
        primary={
            limited_preserve.id: _usage_row(63, limited_preserve.id, window="primary", used_percent=96.0),
            normal.id: _usage_row(64, normal.id, window="primary", used_percent=96.0),
        },
        secondary={
            limited_preserve.id: _usage_row(65, limited_preserve.id, window="secondary", used_percent=96.0),
            normal.id: _usage_row(66, normal.id, window="secondary", used_percent=96.0),
        },
    )

    selection = await balancer.check_opportunistic_admission(
        model=None,
        account_ids=None,
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        budget_threshold_pct=95.0,
    )

    assert selection.account is None
    assert selection.error_code == "opportunistic_burn_window_closed"
    assert selection.error_message == (
        "opportunistic burn window closed: no expendable account has emergency foreground reserve"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["scope", "exclusion", "security"])
async def test_public_selection_applies_candidate_gates(
    selection_cache: AccountSelectionCache,
    gate: str,
) -> None:
    ordinary = _account("contract-ordinary")
    authorized = _account("contract-authorized", security_work_authorized=True)
    primary = {
        ordinary.id: _usage_row(10, ordinary.id, window="primary", used_percent=5.0),
        authorized.id: _usage_row(11, authorized.id, window="primary", used_percent=60.0),
    }
    secondary = {
        ordinary.id: _usage_row(12, ordinary.id, window="secondary", used_percent=10.0),
        authorized.id: _usage_row(13, authorized.id, window="secondary", used_percent=10.0),
    }
    balancer, _, _, _ = _balancer(
        [ordinary, authorized],
        selection_cache,
        primary=primary,
        secondary=secondary,
    )

    unfiltered = await balancer.select_account(routing_strategy="usage_weighted")
    assert unfiltered.account is not None and unfiltered.account.id == ordinary.id

    if gate == "scope":
        filtered = await balancer.select_account(
            account_ids={authorized.id},
            routing_strategy="usage_weighted",
        )
    elif gate == "exclusion":
        filtered = await balancer.select_account(
            exclude_account_ids={ordinary.id},
            routing_strategy="usage_weighted",
        )
    else:
        filtered = await balancer.select_account(
            require_security_work_authorized=True,
            routing_strategy="usage_weighted",
        )

    assert filtered.account is not None and filtered.account.id == authorized.id


@pytest.mark.asyncio
async def test_required_continuity_owner_miss_does_not_mark_healthy_pool_degraded(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_owner = _account("contract-required-owner")
    unavailable_owner.status = AccountStatus.QUOTA_EXCEEDED
    available_alternate = _account("contract-available-alternate")
    balancer, _, _, _ = _balancer(
        [unavailable_owner, available_alternate],
        selection_cache,
    )
    degraded_reasons: list[str] = []
    normal_calls: list[bool] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: normal_calls.append(True))

    selection = await balancer.select_account(
        required_account_id=unavailable_owner.id,
        required_continuity_owner=True,
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_message == "No available accounts"
    assert selection.error_code == load_balancer_module.CONTINUITY_OWNER_UNAVAILABLE
    assert degraded_reasons == []
    assert normal_calls == []


@pytest.mark.asyncio
async def test_deleted_required_continuity_owner_returns_typed_miss_without_global_health_change(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available_alternate = _account("contract-deleted-owner-alternate")
    balancer, _, _, _ = _balancer([available_alternate], selection_cache)
    degraded_reasons: list[str] = []
    normal_calls: list[bool] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: normal_calls.append(True))

    selection = await balancer.select_account(
        required_account_id="contract-deleted-owner",
        required_continuity_owner=True,
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_message == "Required continuity owner account no longer exists"
    assert selection.error_code == load_balancer_module.CONTINUITY_OWNER_UNAVAILABLE
    assert selection.continuity_owner_no_longer_exists is True
    assert degraded_reasons == []
    assert normal_calls == []


@pytest.mark.asyncio
async def test_opportunistic_required_owner_miss_preserves_continuity_classification(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_owner = _account("contract-opportunistic-required-owner")
    unavailable_owner.status = AccountStatus.QUOTA_EXCEEDED
    available_alternate = _account("contract-opportunistic-available-alternate")
    balancer, _, _, _ = _balancer(
        [unavailable_owner, available_alternate],
        selection_cache,
    )
    degraded_reasons: list[str] = []
    normal_calls: list[bool] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: normal_calls.append(True))

    selection = await balancer.select_account(
        required_account_id=unavailable_owner.id,
        required_continuity_owner=True,
        lease_kind="stream",
        traffic_class=load_balancer_module.TRAFFIC_CLASS_OPPORTUNISTIC,
    )

    assert selection.account is None
    assert selection.error_message == "No available accounts"
    assert selection.error_code == load_balancer_module.CONTINUITY_OWNER_UNAVAILABLE
    assert degraded_reasons == []
    assert normal_calls == []


@pytest.mark.asyncio
async def test_opportunistic_policy_block_is_not_classified_as_owner_unavailable(
    selection_cache: AccountSelectionCache,
) -> None:
    preserve_owner = _account("contract-opportunistic-preserve-owner")
    preserve_owner.routing_policy = "preserve"
    available_alternate = _account("contract-opportunistic-policy-alternate")
    balancer, _, _, _ = _balancer(
        [preserve_owner, available_alternate],
        selection_cache,
        primary={
            preserve_owner.id: _usage_row(100, preserve_owner.id, window="primary", used_percent=92.0),
            available_alternate.id: _usage_row(
                101,
                available_alternate.id,
                window="primary",
                used_percent=10.0,
            ),
        },
        secondary={
            preserve_owner.id: _usage_row(102, preserve_owner.id, window="secondary", used_percent=20.0),
            available_alternate.id: _usage_row(
                103,
                available_alternate.id,
                window="secondary",
                used_percent=10.0,
            ),
        },
    )

    selection = await balancer.select_account(
        required_account_id=preserve_owner.id,
        required_continuity_owner=True,
        routing_strategy="usage_weighted",
        lease_kind="stream",
        traffic_class=load_balancer_module.TRAFFIC_CLASS_OPPORTUNISTIC,
    )

    assert selection.account is None
    assert selection.error_message == (
        "opportunistic burn window closed: preserve floor or stale usage data blocks opportunistic burn"
    )
    assert selection.error_code == load_balancer_module.OPPORTUNISTIC_BURN_WINDOW_CLOSED


@pytest.mark.asyncio
async def test_required_file_owner_miss_does_not_mark_healthy_pool_degraded(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_owner = _account("contract-file-owner")
    unavailable_owner.status = AccountStatus.QUOTA_EXCEEDED
    available_alternate = _account("contract-file-alternate")
    balancer, _, _, _ = _balancer(
        [unavailable_owner, available_alternate],
        selection_cache,
    )
    degraded_reasons: list[str] = []
    normal_calls: list[bool] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: normal_calls.append(True))

    selection = await balancer.select_account(
        required_account_id=unavailable_owner.id,
        required_account_is_ownership_constraint=True,
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_message == "No available accounts"
    assert degraded_reasons == []
    assert normal_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_gate", ["scope", "security"])
async def test_required_continuity_owner_policy_conflict_does_not_fallback(
    selection_cache: AccountSelectionCache,
    policy_gate: str,
) -> None:
    owner = _account("contract-policy-owner")
    alternate = _account("contract-policy-alternate", security_work_authorized=True)
    balancer, _, _, _ = _balancer([owner, alternate], selection_cache)

    selection = await balancer.select_account(
        account_ids={alternate.id} if policy_gate == "scope" else None,
        required_account_id=owner.id,
        required_account_is_ownership_constraint=True,
        required_continuity_owner=True,
        require_security_work_authorized=policy_gate == "security",
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_code == load_balancer_module.CONTINUITY_OWNER_POLICY_CONFLICT


@pytest.mark.asyncio
async def test_required_continuity_owner_preserves_empty_security_policy_error(
    selection_cache: AccountSelectionCache,
) -> None:
    owner = _account("contract-unauthorized-owner")
    balancer, _, _, _ = _balancer([owner], selection_cache)

    selection = await balancer.select_account(
        required_account_id=owner.id,
        required_account_is_ownership_constraint=True,
        required_continuity_owner=True,
        require_security_work_authorized=True,
    )

    assert selection.account is None
    assert selection.error_code == "no_security_work_authorized_accounts"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("required_account_is_ownership_constraint", "required_continuity_owner"),
    [
        (True, False),
        (False, True),
    ],
)
async def test_required_account_ownership_flags_require_an_account_id(
    selection_cache: AccountSelectionCache,
    required_account_is_ownership_constraint: bool,
    required_continuity_owner: bool,
) -> None:
    balancer, _, _, _ = _balancer([_account("contract-owner")], selection_cache)

    with pytest.raises(ValueError, match="require required_account_id"):
        await balancer.select_account(
            required_account_is_ownership_constraint=required_account_is_ownership_constraint,
            required_continuity_owner=required_continuity_owner,
        )


@pytest.mark.asyncio
async def test_hard_sticky_owner_miss_does_not_mark_healthy_pool_degraded(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_owner = _account("contract-sticky-owner")
    unavailable_owner.status = AccountStatus.QUOTA_EXCEEDED
    available_alternate = _account("contract-sticky-alternate")
    balancer, _, _, sticky_repo = _balancer(
        [unavailable_owner, available_alternate],
        selection_cache,
    )
    sticky_repo.account_id = unavailable_owner.id
    degraded_reasons: list[str] = []
    normal_calls: list[bool] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: normal_calls.append(True))

    selection = await balancer.select_account(
        sticky_key="hard-owned-turn-state",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_code == "hard_affinity_saturated"
    assert degraded_reasons == []
    assert normal_calls == []


@pytest.mark.asyncio
async def test_required_continuity_owner_preserves_transient_hard_affinity_saturation(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _account("contract-required-sticky-owner")
    balancer, _, _, sticky_repo = _balancer([owner], selection_cache)
    sticky_repo.account_id = owner.id

    async def saturated_selection(_owner: object, *, request: Any) -> object:
        return SimpleNamespace(
            selection_inputs=request.selection_inputs,
            selected_snapshot=None,
            selected_lease=None,
            error_message="Hard affinity owner account is unavailable",
            error_code="hard_affinity_saturated",
            resets_at=None,
            disposition="shared_result",
        )

    monkeypatch.setattr(load_balancer_module, "run_sticky_selection_path", saturated_selection)

    selection = await balancer.select_account(
        sticky_key="hard-required-owned-turn-state",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        required_account_id=owner.id,
        required_continuity_owner=True,
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_message == "Hard affinity owner account is unavailable"
    assert selection.error_code == "hard_affinity_saturated"


@pytest.mark.asyncio
async def test_hard_sticky_owner_miss_with_optional_preferred_owner_preserves_global_health(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_owner = _account("contract-sticky-preferred-owner")
    unavailable_owner.status = AccountStatus.QUOTA_EXCEEDED
    available_alternate = _account("contract-sticky-preferred-alternate")
    balancer, _, _, sticky_repo = _balancer(
        [unavailable_owner, available_alternate],
        selection_cache,
    )
    sticky_repo.account_id = unavailable_owner.id
    degraded_reasons: list[str] = []
    normal_calls: list[bool] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: normal_calls.append(True))

    selection = await balancer.select_account(
        sticky_key="hard-owned-preferred-turn-state",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        required_account_id=unavailable_owner.id,
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_code == "hard_affinity_saturated"
    assert degraded_reasons == []
    assert normal_calls == []


@pytest.mark.asyncio
async def test_hard_sticky_owner_miss_under_single_account_routing_preserves_global_health(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_owner = _account("contract-sticky-single-owner")
    unavailable_owner.status = AccountStatus.QUOTA_EXCEEDED
    available_alternate = _account("contract-sticky-single-alternate")
    balancer, _, _, sticky_repo = _balancer(
        [unavailable_owner, available_alternate],
        selection_cache,
    )
    sticky_repo.account_id = unavailable_owner.id
    degraded_reasons: list[str] = []
    normal_calls: list[bool] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)
    monkeypatch.setattr(load_balancer_module, "set_normal", lambda: normal_calls.append(True))

    selection = await balancer.select_account(
        sticky_key="hard-owned-single-turn-state",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        required_account_id=unavailable_owner.id,
        routing_strategy="single_account",
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_code == "hard_affinity_saturated"
    assert degraded_reasons == []
    assert normal_calls == []


@pytest.mark.asyncio
async def test_configured_single_account_miss_marks_service_degraded(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_account = _account("contract-configured-account")
    unavailable_account.status = AccountStatus.QUOTA_EXCEEDED
    available_alternate = _account("contract-available-alternate")
    balancer, _, _, _ = _balancer(
        [unavailable_account, available_alternate],
        selection_cache,
    )
    degraded_reasons: list[str] = []
    monkeypatch.setattr(load_balancer_module, "set_degraded", degraded_reasons.append)

    selection = await balancer.select_account(
        required_account_id=unavailable_account.id,
        lease_kind="stream",
    )

    assert selection.account is None
    assert selection.error_message is not None
    assert "degraded mode" in selection.error_message
    assert degraded_reasons == ["all upstream accounts are unavailable"]


@pytest.mark.asyncio
async def test_public_selection_reports_an_empty_security_pool(selection_cache: AccountSelectionCache) -> None:
    balancer, _, _, _ = _balancer([_account("contract-unauthorized")], selection_cache)

    selection = await balancer.select_account(require_security_work_authorized=True)

    assert selection.account is None
    assert selection.error_code == "no_security_work_authorized_accounts"
    assert selection.error_message == "No accounts marked as authorized for security work"


@pytest.mark.asyncio
async def test_public_selection_prefers_the_primary_budget_safe_account(
    selection_cache: AccountSelectionCache,
) -> None:
    budget_safe = _account("contract-budget-safe")
    primary_pressured = _account("contract-primary-pressured")
    primary = {
        budget_safe.id: _usage_row(20, budget_safe.id, window="primary", used_percent=10.0),
        primary_pressured.id: _usage_row(21, primary_pressured.id, window="primary", used_percent=60.0),
    }
    secondary = {
        budget_safe.id: _usage_row(22, budget_safe.id, window="secondary", used_percent=80.0),
        primary_pressured.id: _usage_row(23, primary_pressured.id, window="secondary", used_percent=5.0),
    }
    balancer, _, _, _ = _balancer(
        [budget_safe, primary_pressured],
        selection_cache,
        primary=primary,
        secondary=secondary,
    )

    selection = await balancer.select_account(
        routing_strategy="usage_weighted",
        budget_threshold_pct=50.0,
    )

    assert selection.account is not None
    assert selection.account.id == budget_safe.id


@pytest.mark.asyncio
async def test_selection_cache_is_scoped_by_account_ids_and_service_tier(
    selection_cache: AccountSelectionCache,
) -> None:
    account_a = _account("contract-cache-a")
    account_b = _account("contract-cache-b")
    balancer, accounts_repo, _, _ = _balancer([account_a, account_b], selection_cache)

    first_a = await balancer._load_selection_inputs(model=None, account_ids={account_a.id})
    second_a = await balancer._load_selection_inputs(model=None, account_ids={account_a.id})
    only_b = await balancer._load_selection_inputs(model=None, account_ids={account_b.id})
    flex_b = await balancer._load_selection_inputs(
        model=None,
        service_tier="flex",
        account_ids={account_b.id},
    )
    cached_flex_b = await balancer._load_selection_inputs(
        model=None,
        service_tier="flex",
        account_ids={account_b.id},
    )
    priority_b = await balancer._load_selection_inputs(
        model=None,
        service_tier="priority",
        account_ids={account_b.id},
    )
    cached_priority_b = await balancer._load_selection_inputs(
        model=None,
        service_tier="priority",
        account_ids={account_b.id},
    )

    assert [account.id for account in first_a.accounts] == [account_a.id]
    assert [account.id for account in second_a.accounts] == [account_a.id]
    assert [account.id for account in only_b.accounts] == [account_b.id]
    assert [account.id for account in flex_b.accounts] == [account_b.id]
    assert [account.id for account in cached_flex_b.accounts] == [account_b.id]
    assert [account.id for account in priority_b.accounts] == [account_b.id]
    assert [account.id for account in cached_priority_b.accounts] == [account_b.id]
    assert accounts_repo.list_calls == 4


@pytest.mark.asyncio
async def test_cached_selection_inputs_are_mutation_isolated(selection_cache: AccountSelectionCache) -> None:
    account = _account("contract-clone")
    primary = _usage_row(1, account.id, window="primary", used_percent=10.0)
    secondary = _usage_row(2, account.id, window="secondary", used_percent=20.0)
    monthly = _usage_row(3, account.id, window="monthly", used_percent=30.0)
    balancer, _, usage_repo, _ = _balancer(
        [account],
        selection_cache,
        primary={account.id: primary},
        secondary={account.id: secondary},
        monthly={account.id: monthly},
    )

    first = await balancer._load_selection_inputs(model=None)
    first.accounts[0].status = AccountStatus.PAUSED
    first.latest_primary[account.id].used_percent = 91.0
    assert first.runtime_accounts is not None
    first.runtime_accounts[0].status = AccountStatus.DEACTIVATED

    second = await balancer._load_selection_inputs(model=None)
    assert second.accounts[0].status == AccountStatus.ACTIVE
    assert second.latest_primary[account.id].used_percent == 10.0
    assert second.runtime_accounts is not None
    assert second.runtime_accounts[0].status == AccountStatus.ACTIVE

    second.accounts[0].status = AccountStatus.PAUSED
    second.latest_secondary[account.id].used_percent = 92.0
    second.latest_monthly[account.id].used_percent = 93.0

    third = await balancer._load_selection_inputs(model=None)
    assert third.accounts[0].status == AccountStatus.ACTIVE
    assert third.latest_primary[account.id].used_percent == 10.0
    assert third.latest_secondary[account.id].used_percent == 20.0
    assert third.latest_monthly[account.id].used_percent == 30.0
    assert usage_repo.calls == {"primary": 1, "secondary": 1, "monthly": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["non-sticky", "sticky"])
async def test_selection_releases_lease_when_persistence_fails(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-failure-{sticky}")
    balancer, _, _, sticky_repo = _balancer([account], selection_cache)
    persist_calls = 0

    async def fail_persist(*_args: Any, **_kwargs: Any) -> set[str]:
        nonlocal persist_calls
        persist_calls += 1
        assert await balancer.account_pressure_snapshot(account.id) == (0, 1, 42.0)
        raise RuntimeError("persistence failed")

    release_spy = AsyncMock(wraps=balancer.release_account_lease)
    monkeypatch.setattr(balancer, "_persist_selection_state", fail_persist)
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    with pytest.raises(RuntimeError, match="persistence failed"):
        await _select_with_lease(balancer, sticky=sticky)

    assert persist_calls == 1
    release_spy.assert_awaited_once()
    release_call = release_spy.await_args
    assert release_call is not None
    assert release_call.args[0] is not None
    assert sticky_repo.get_calls == (1 if sticky else 0)
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["non-sticky", "sticky"])
async def test_selection_releases_lease_when_persistence_is_cancelled(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-cancel-{sticky}")
    balancer, _, _, sticky_repo = _balancer([account], selection_cache)
    persist_started = asyncio.Event()
    persist_blocker = asyncio.Event()

    async def block_persist(*_args: Any, **_kwargs: Any) -> set[str]:
        assert await balancer.account_pressure_snapshot(account.id) == (0, 1, 42.0)
        persist_started.set()
        await persist_blocker.wait()
        return set()

    release_spy = AsyncMock(wraps=balancer.release_account_lease)
    monkeypatch.setattr(balancer, "_persist_selection_state", block_persist)
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    selection_task = asyncio.create_task(_select_with_lease(balancer, sticky=sticky))
    await asyncio.wait_for(persist_started.wait(), timeout=2.0)
    assert selection_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await selection_task

    assert selection_task.cancelled()
    release_spy.assert_awaited_once()
    release_call = release_spy.await_args
    assert release_call is not None
    assert release_call.args[0] is not None
    assert sticky_repo.get_calls == (1 if sticky else 0)
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["non-sticky", "sticky"])
async def test_stale_selection_retries_do_not_leak_leases(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-stale-{sticky}")
    balancer, _, _, sticky_repo = _balancer([account], selection_cache)
    original_load = balancer._load_selection_inputs
    load_spy = AsyncMock(side_effect=original_load)
    monkeypatch.setattr(balancer, "_load_selection_inputs", load_spy)
    persist_calls = 0

    async def always_stale(*_args: Any, **_kwargs: Any) -> set[str]:
        nonlocal persist_calls
        persist_calls += 1
        assert await balancer.account_pressure_snapshot(account.id) == (0, 1, 42.0)
        return {account.id}

    release_spy = AsyncMock(wraps=balancer.release_account_lease)
    monkeypatch.setattr(balancer, "_persist_selection_state", always_stale)
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    selection = await _select_with_lease(balancer, sticky=sticky)

    assert persist_calls == 4
    assert load_spy.await_count == 4
    assert selection.account is None
    assert selection.lease is None
    assert release_spy.await_count == persist_calls
    released_leases = [release_call.args[0] for release_call in release_spy.await_args_list]
    assert all(lease is not None for lease in released_leases)
    assert len({lease.lease_id for lease in released_leases if lease is not None}) == persist_calls
    assert sticky_repo.get_calls == (4 if sticky else 0)
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)

    replacement = await balancer.acquire_account_lease(
        account.id,
        kind="stream",
        concurrency_caps=_CONCURRENCY_CAPS,
    )
    assert replacement is not None
    await balancer.release_account_lease(replacement)
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
async def test_non_sticky_cache_generation_change_reselects_and_releases_once(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _account("contract-cache-generation")
    balancer, _, _, _ = _balancer([account], selection_cache)
    original_load = balancer._load_selection_inputs
    load_spy = AsyncMock(side_effect=original_load)
    release_spy = AsyncMock(wraps=balancer.release_account_lease)
    persist_calls = 0

    async def invalidate_during_first_persist(*_args: Any, **_kwargs: Any) -> set[str]:
        nonlocal persist_calls
        persist_calls += 1
        assert await balancer.account_pressure_snapshot(account.id) == (0, 1, 42.0)
        if persist_calls == 1:
            selection_cache.invalidate()
        return set()

    monkeypatch.setattr(balancer, "_load_selection_inputs", load_spy)
    monkeypatch.setattr(balancer, "_persist_selection_state", invalidate_during_first_persist)
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    selection = await _select_with_lease(balancer, sticky=False)

    assert selection.account is not None
    assert selection.account.id == account.id
    assert selection.lease is not None
    assert persist_calls == 2
    assert load_spy.await_count == 2
    release_spy.assert_awaited_once()
    release_call = release_spy.await_args
    assert release_call is not None
    released_lease = release_call.args[0]
    assert released_lease is not None
    assert released_lease.lease_id != selection.lease.lease_id
    assert await balancer.account_pressure_snapshot(account.id) == (0, 1, 42.0)

    await balancer.release_account_lease(selection.lease)
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
async def test_fresh_owner_usage_check_uses_one_account_scoped_snapshot(
    selection_cache: AccountSelectionCache,
) -> None:
    account = _account("contract-owner-usage-snapshot")
    account.usage_limit_enabled = True
    account.usage_limit_percent = 10.0
    balancer, _, usage_repo, _ = _balancer(
        [account],
        selection_cache,
        primary={account.id: _usage_row(81, account.id, window="primary", used_percent=10.0)},
    )

    state = await balancer.authorize_account_fresh(account.id)

    assert state.kind is OwnerAuthorizationKind.USAGE_POLICY_BLOCKED
    assert state.usage_limit_state is AccountUsageLimitState.REACHED
    assert usage_repo.snapshot_calls == 1
    assert sum(usage_repo.calls.values()) == 0


@pytest.mark.asyncio
async def test_fresh_owner_usage_check_fails_closed_for_unavailable_owner(
    selection_cache: AccountSelectionCache,
) -> None:
    account = _account("contract-owner-usage-unavailable")
    account.status = AccountStatus.PAUSED
    balancer, _, usage_repo, _ = _balancer(
        [account],
        selection_cache,
    )

    assert (await balancer.authorize_account_fresh(account.id)).kind is OwnerAuthorizationKind.OWNER_UNAVAILABLE
    assert usage_repo.snapshot_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_selection_rechecks_usage_limit_when_inputs_change_before_persist(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-input-generation-pre-persist-{sticky}")
    account.usage_limit_enabled = True
    account.usage_limit_percent = 10.0
    initial_usage = _usage_row(80, account.id, window="primary", used_percent=5.0)
    balancer, _, usage_repo, sticky_repo = _balancer(
        [account],
        selection_cache,
        primary={account.id: initial_usage},
    )
    original_prepare = balancer._prepare_sticky_selection_states
    input_changed = False

    def cross_limit_after_input_load(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[list[AccountState], dict[str, Account]]:
        nonlocal input_changed
        prepared = original_prepare(*args, **kwargs)
        if not input_changed:
            input_changed = True
            usage_repo.rows["primary"][account.id] = _usage_row(
                81,
                account.id,
                window="primary",
                used_percent=10.0,
            )
            selection_cache.invalidate()
        return prepared

    monkeypatch.setattr(balancer, "_prepare_sticky_selection_states", cross_limit_after_input_load)

    selection = await asyncio.wait_for(_select_with_lease(balancer, sticky=sticky), timeout=1.0)

    assert input_changed
    assert selection.account is None
    assert selection.lease is None
    assert selection.error_code == "account_usage_limit_reached"
    assert sticky_repo.account_id is None
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_selection_rechecks_usage_limit_when_inputs_change_during_persist(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-input-generation-limit-{sticky}")
    account.usage_limit_enabled = True
    account.usage_limit_percent = 10.0
    initial_usage = _usage_row(80, account.id, window="primary", used_percent=5.0)
    balancer, _, usage_repo, sticky_repo = _balancer(
        [account],
        selection_cache,
        primary={account.id: initial_usage},
    )
    load_spy = AsyncMock(side_effect=balancer._load_selection_inputs)
    persist_calls = 0

    async def cross_limit_during_first_persist(*_args: Any, **_kwargs: Any) -> set[str]:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 1:
            usage_repo.rows["primary"][account.id] = _usage_row(
                81,
                account.id,
                window="primary",
                used_percent=10.0,
            )
            selection_cache.invalidate()
        return set()

    release_spy = AsyncMock(wraps=balancer.release_account_lease)
    monkeypatch.setattr(balancer, "_load_selection_inputs", load_spy)
    monkeypatch.setattr(balancer, "_persist_selection_state", cross_limit_during_first_persist)
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    selection = await asyncio.wait_for(_select_with_lease(balancer, sticky=sticky), timeout=1.0)

    assert selection.account is None
    assert selection.lease is None
    assert selection.error_code == "account_usage_limit_reached"
    # A deterministic policy denial does not burn the remaining retry budget;
    # only the first attempt acquired a stale lease.
    assert load_spy.await_count == 2
    release_spy.assert_awaited_once()
    release_call = release_spy.await_args
    assert release_call is not None
    released_lease = release_call.args[0]
    assert released_lease is not None
    assert sticky_repo.account_id is None
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_selection_bounds_continuous_input_generation_changes(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-input-generation-churn-{sticky}")
    balancer, _, usage_repo, sticky_repo = _balancer([account], selection_cache)
    original_last_selected_at = 123.0
    balancer._runtime[account.id] = load_balancer_module.RuntimeState(
        last_selected_at=original_last_selected_at,
    )
    original_load = balancer._load_selection_inputs
    load_spy = AsyncMock(side_effect=original_load)
    release_spy = AsyncMock(wraps=balancer.release_account_lease)
    persist_calls = 0

    async def invalidate_after_each_admission(*_args: Any, **_kwargs: Any) -> set[str]:
        nonlocal persist_calls
        persist_calls += 1
        selection_cache.invalidate()
        return set()

    monkeypatch.setattr(balancer, "_load_selection_inputs", load_spy)
    monkeypatch.setattr(balancer, "_persist_selection_state", invalidate_after_each_admission)
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    selection = await asyncio.wait_for(_select_with_lease(balancer, sticky=sticky), timeout=1.0)

    assert selection.account is not None
    assert selection.account.id == account.id
    assert selection.lease is not None
    assert selection.error_code is None
    assert persist_calls == 4
    assert load_spy.await_count == 4
    assert release_spy.await_count == 3
    assert usage_repo.snapshot_calls == 1
    assert sticky_repo.account_id == (account.id if sticky else None)
    # The cursor is replica-local fairness state, so each locally admitted
    # attempt consumes a turn even when a newer policy snapshot supersedes it.
    last_selected_at = balancer._runtime[account.id].last_selected_at
    assert last_selected_at is not None
    assert last_selected_at > original_last_selected_at
    assert await balancer.account_pressure_snapshot(account.id) == (0, 1, 42.0)

    await balancer.release_account_lease(selection.lease)
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_selection_final_generation_check_rejects_newly_reached_usage_limit(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-input-generation-final-limit-{sticky}")
    account.usage_limit_enabled = True
    account.usage_limit_percent = 10.0
    initial_usage = _usage_row(80, account.id, window="primary", used_percent=5.0)
    balancer, _, usage_repo, sticky_repo = _balancer(
        [account],
        selection_cache,
        primary={account.id: initial_usage},
    )
    load_spy = AsyncMock(side_effect=balancer._load_selection_inputs)
    release_spy = AsyncMock(wraps=balancer.release_account_lease)
    persist_calls = 0

    async def invalidate_and_cross_limit_on_final_attempt(*_args: Any, **_kwargs: Any) -> set[str]:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 4:
            usage_repo.rows["primary"][account.id] = _usage_row(
                81,
                account.id,
                window="primary",
                used_percent=10.0,
            )
        selection_cache.invalidate()
        return set()

    monkeypatch.setattr(balancer, "_load_selection_inputs", load_spy)
    monkeypatch.setattr(balancer, "_persist_selection_state", invalidate_and_cross_limit_on_final_attempt)
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    selection = await asyncio.wait_for(_select_with_lease(balancer, sticky=sticky), timeout=1.0)

    assert selection.account is None
    assert selection.lease is None
    assert selection.error_code == "account_usage_limit_reached"
    assert persist_calls == 4
    assert load_spy.await_count == 4
    assert release_spy.await_count == 4
    assert usage_repo.snapshot_calls == 1
    assert sticky_repo.account_id is None
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_public_selection_final_generation_check_releases_lease_when_usage_read_fails(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account(f"contract-input-generation-final-read-failure-{sticky}")
    balancer, _, _, sticky_repo = _balancer([account], selection_cache)
    release_spy = AsyncMock(wraps=balancer.release_account_lease)

    async def invalidate_after_each_admission(*_args: Any, **_kwargs: Any) -> set[str]:
        selection_cache.invalidate()
        return set()

    usage_read_error = RuntimeError("usage snapshot unavailable")
    monkeypatch.setattr(balancer, "_persist_selection_state", invalidate_after_each_admission)
    monkeypatch.setattr(
        balancer,
        "authorize_account_fresh",
        AsyncMock(side_effect=usage_read_error),
    )
    monkeypatch.setattr(balancer, "release_account_lease", release_spy)

    with pytest.raises(RuntimeError, match="usage snapshot unavailable"):
        await asyncio.wait_for(_select_with_lease(balancer, sticky=sticky), timeout=1.0)

    assert release_spy.await_count == 4
    assert sticky_repo.account_id is None
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
@pytest.mark.parametrize(
    "new_status",
    [AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED, None],
    ids=["paused", "deactivated", "reauth", "deleted"],
)
async def test_final_attempt_unavailable_owner_never_publishes_affinity_or_leaks_pressure(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
    new_status: AccountStatus | None,
) -> None:
    account = _account("final-owner-unavailable")
    accounts = [account]
    balancer, _, usage_repo, sticky_repo = _balancer(accounts, selection_cache)
    persist_calls = 0
    upsert = AsyncMock(wraps=sticky_repo.upsert)
    monkeypatch.setattr(sticky_repo, "upsert", upsert)

    async def mutate_at_final_persist(*_args: Any, **_kwargs: Any) -> set[str]:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 4:
            if new_status is None:
                accounts.clear()
            else:
                account.status = new_status
        selection_cache.invalidate()
        return set()

    monkeypatch.setattr(balancer, "_persist_selection_state", mutate_at_final_persist)
    result = await asyncio.wait_for(_select_with_lease(balancer, sticky=sticky), timeout=2)
    assert persist_calls == 4
    assert usage_repo.snapshot_calls == 1
    assert result.account is None
    assert result.lease is None
    assert result.error_code == "preferred_account_unavailable"
    assert sticky_repo.account_id is None
    upsert.assert_not_awaited()
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_final_authorization_database_failure_is_local_and_releases_pressure(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account("final-owner-database-error")
    balancer, _, usage_repo, sticky_repo = _balancer([account], selection_cache)

    async def invalidate(*_args: Any, **_kwargs: Any) -> set[str]:
        selection_cache.invalidate()
        return set()

    monkeypatch.setattr(balancer, "_persist_selection_state", invalidate)
    monkeypatch.setattr(usage_repo, "account_usage_limit_snapshot", AsyncMock(side_effect=RuntimeError("read failed")))
    result = await _select_with_lease(balancer, sticky=sticky)
    assert result.account is None
    assert result.lease is None
    assert result.error_code == "account_usage_limit_authorization_failed"
    status, payload = selection_failure_response(result)
    assert status == 503
    assert payload["error"]["code"] == "account_usage_limit_authorization_failed"
    assert sticky_repo.account_id is None
    assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)


@pytest.mark.parametrize("traffic_class", ["foreground", "opportunistic"])
@pytest.mark.parametrize(
    "peer_status",
    [AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED],
)
def test_disabled_usage_policy_projection_preserves_canonical_fallback_context(traffic_class, peer_status):
    from app.modules.proxy._load_balancer.sticky_selection import _filter_states_for_usage_limit_and_account_caps

    now = time.time()
    states = [
        AccountState(
            account_id="backoff",
            status=AccountStatus.ACTIVE,
            used_percent=5,
            secondary_used_percent=5,
            routing_policy="burn_first",
            error_count=ERROR_BACKOFF_THRESHOLD,
            last_error_at=now,
        ),
        AccountState(account_id="peer", status=peer_status, used_percent=5, reset_at=now + 3600),
    ]
    expected = select_account([replace(state) for state in states], now=now, traffic_class=traffic_class)
    pool = evaluate_routing_pool(states, now=now, traffic_class=traffic_class)
    assert [state.account_id for state in pool.routable_candidates] == ["backoff"]
    assert all(state.account_id != "peer" for state in pool.capacity_candidates)
    filtered, exhausted = _filter_states_for_usage_limit_and_account_caps(
        states,
        lease_kind="stream",
        caps=_CONCURRENCY_CAPS,
        traffic_class=traffic_class,
        pool=pool,
    )
    actual = select_account(filtered, now=now, traffic_class=traffic_class)
    assert not exhausted
    assert actual == expected
    assert [state.account_id for state in filtered] == ["backoff", "peer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
@pytest.mark.parametrize(
    ("peer_status", "admits_backoff"),
    [
        (AccountStatus.PAUSED, False),
        (AccountStatus.DEACTIVATED, False),
        (AccountStatus.RATE_LIMITED, True),
        (AccountStatus.QUOTA_EXCEEDED, True),
    ],
)
async def test_disabled_usage_policy_public_fallback_matches_pre_feature_base(
    selection_cache: AccountSelectionCache,
    sticky: bool,
    peer_status: AccountStatus,
    admits_backoff: bool,
) -> None:
    # Verified on base 5ad638b6: paused/deactivated accounts were already removed
    # during account loading. Quota/rate-limited peers reach canonical selection
    # and must retain their hard-block evidence through the new cap projection.
    active, peer = _account("baseline-backoff"), _account("baseline-peer")
    peer.status = peer_status
    peer.reset_at = int(time.time()) + 3600
    balancer, _, _, _ = _balancer([active, peer], selection_cache)
    balancer._runtime[active.id] = load_balancer_module.RuntimeState(
        error_count=ERROR_BACKOFF_THRESHOLD,
        last_error_at=time.time(),
    )
    result = await _select_with_lease(balancer, sticky=sticky)
    try:
        assert (result.account is not None) is admits_backoff
        if result.account is not None:
            assert result.account.id == active.id
    finally:
        await balancer.release_account_lease(result.lease)
    assert await balancer.account_pressure_snapshot(active.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky", [False, True], ids=["unbound", "sticky"])
async def test_final_authorization_repeated_cancellation_releases_selection_pressure(
    selection_cache: AccountSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    sticky: bool,
) -> None:
    account = _account("final-repeated-cancellation")
    balancer, _, _, sticky_repo = _balancer([account], selection_cache)
    authorization_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    original_release = balancer.release_account_lease
    final_lease = None

    async def invalidate(*_args: Any, **_kwargs: Any) -> set[str]:
        selection_cache.invalidate()
        return set()

    async def authorize(_account_id: str):
        authorization_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled authorization must not grant permission")

    async def release(lease):
        nonlocal final_lease
        if authorization_started.is_set():
            final_lease = lease
            cleanup_started.set()
        await original_release(lease)

    monkeypatch.setattr(balancer, "_persist_selection_state", invalidate)
    monkeypatch.setattr(balancer, "authorize_account_fresh", authorize)
    monkeypatch.setattr(balancer, "release_account_lease", release)
    task = asyncio.create_task(_select_with_lease(balancer, sticky=sticky))
    try:
        await asyncio.wait_for(authorization_started.wait(), timeout=2)
        async with balancer._runtime_lock:
            task.cancel()
            await asyncio.wait_for(cleanup_started.wait(), timeout=2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert sticky_repo.account_id is None
        assert await balancer.account_pressure_snapshot(account.id) == (0, 0, 0.0)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await original_release(final_lease)
