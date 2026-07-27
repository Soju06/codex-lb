from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.load_balancer as load_balancer_module
from app.db.models import Account, AccountStatus, StickySession, StickySessionKind, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.account_cache import AccountSelectionCache
from app.modules.proxy.load_balancer import AccountConcurrencyCaps, AccountSelection, LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

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
        primary: dict[str, UsageHistory] | None = None,
        secondary: dict[str, UsageHistory] | None = None,
        monthly: dict[str, UsageHistory] | None = None,
    ) -> None:
        self.rows = {
            "primary": primary or {},
            "secondary": secondary or {},
            "monthly": monthly or {},
        }
        self.calls = {"primary": 0, "secondary": 0, "monthly": 0}

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


class _StickySessionsRepository:
    def __init__(self) -> None:
        self.account_id: str | None = None
        self.get_calls = 0

    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
        del args, kwargs
        self.get_calls += 1
        return self.account_id

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
    usage_repo = _UsageRepository(primary=primary, secondary=secondary, monthly=monthly)
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
