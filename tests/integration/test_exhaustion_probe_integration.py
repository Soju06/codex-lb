"""Pool-exhaustion probe against the real balancer and database (#2123 WP-C1 P4).

Each test seeds accounts the way production state arrives (statuses, usage
windows, block markers), asks the probe through a real ``ProxyService`` and
compares its answer with the foreground selection the same request would run.
The probe must agree with selection on *whether* the pool is exhausted and on
``resets_at``, must flip with ``service_tier`` exactly when tier filtering
removes the last healthy account, and must leave runtime, lease and persisted
account state untouched.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.balancer import USAGE_LIMIT_REACHED
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySession
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy._load_balancer.exhaustion_probe import PoolExhaustion, probe_pool_usage_exhaustion
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.selection_errors import selection_failure_response
from app.modules.proxy.service import ProxyService
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration

_MODEL = "gpt-5.4"
_SELECTION_BUDGET_SECONDS = 30.0


@asynccontextmanager
async def _repo_factory() -> AsyncIterator[ProxyRepositories]:
    async with SessionLocal() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            request_logs=RequestLogsRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=ApiKeysRepository(session),
            additional_usage=AdditionalUsageRepository(session),
        )


def _account(
    account_id: str,
    *,
    plan_type: str = "plus",
    status: AccountStatus = AccountStatus.ACTIVE,
    reset_at: int | None = None,
    blocked_at: int | None = None,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
        refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
        id_token_encrypted=encryptor.encrypt(f"id-{account_id}"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
        reset_at=reset_at,
        blocked_at=blocked_at,
    )


async def _seed(
    accounts: list[tuple[Account, float, float]],
    *,
    primary_reset_at: int,
    secondary_reset_at: int,
) -> None:
    """Upsert accounts with fresh primary/secondary usage rows recorded now."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        for account, primary_used, secondary_used in accounts:
            await accounts_repo.upsert(account)
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=primary_used,
                window="primary",
                reset_at=primary_reset_at,
                window_minutes=300,
                recorded_at=now,
            )
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=secondary_used,
                window="secondary",
                reset_at=secondary_reset_at,
                window_minutes=10080,
                recorded_at=now,
            )
        await session.commit()
    get_account_selection_cache().invalidate()


async def _exhaust_persisted(account_id: str, *, now_epoch: int, reset_at: int) -> None:
    """Move an already-seeded account into usage-proven exhaustion the way the runtime records it."""
    now = utcnow()
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.status = AccountStatus.QUOTA_EXCEEDED
        account.blocked_at = now_epoch
        account.reset_at = reset_at
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=account_id,
            used_percent=100.0,
            window="primary",
            reset_at=reset_at,
            window_minutes=300,
            recorded_at=now,
        )
        await usage_repo.add_entry(
            account_id=account_id,
            used_percent=30.0,
            window="secondary",
            reset_at=reset_at + 6 * 86400,
            window_minutes=10080,
            recorded_at=now,
        )
        await session.commit()
    get_account_selection_cache().invalidate()


def _exhausted(account_id: str, *, now_epoch: int, reset_at: int, plan_type: str = "plus") -> Account:
    # Mirror handle_quota_exceeded: status + blocked_at marker + reset deadline.
    return _account(
        account_id,
        plan_type=plan_type,
        status=AccountStatus.QUOTA_EXCEEDED,
        reset_at=reset_at,
        blocked_at=now_epoch,
    )


async def _foreground_selection(
    service: ProxyService, *, model: str | None, service_tier: str | None = None
) -> AccountSelection:
    """The foreground selection a Responses request for ``model`` at ``service_tier`` would run."""
    return await service._select_account_with_budget(
        service._clock.monotonic() + _SELECTION_BUDGET_SECONDS,
        request_id="probe-parity",
        kind="responses",
        model=model,
        service_tier=service_tier,
    )


async def _probe(
    service: ProxyService, *, model: str | None, service_tier: str | None = None, settings: object | None = None
) -> PoolExhaustion | None:
    """Probe with the settings snapshot the routing stage hands it (the cached dashboard settings by default)."""
    snapshot = settings if settings is not None else await get_settings_cache().get()
    return await probe_pool_usage_exhaustion(
        service, settings=snapshot, api_key=None, model=model, service_tier=service_tier
    )


async def _probe_leaving_runtime_untouched(
    service: ProxyService, *, model: str | None, service_tier: str | None = None, settings: object | None = None
) -> PoolExhaustion | None:
    """Probe and assert the live balancer runtime is byte-identical before and after."""
    runtime = service._load_balancer._runtime
    runtime_before = deepcopy(runtime)
    exhaustion = await _probe(service, model=model, service_tier=service_tier, settings=settings)
    assert runtime == runtime_before, "the probe must not create, lease, refresh or mark any runtime entry"
    return exhaustion


async def _persisted_account_rows() -> dict[str, tuple[AccountStatus, int | None, int | None]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Account))).scalars().all()
        return {row.id: (row.status, row.reset_at, row.blocked_at) for row in rows}


async def _sticky_row_count() -> int:
    async with SessionLocal() as session:
        return int((await session.execute(select(func.count()).select_from(StickySession))).scalar_one())


@pytest.mark.asyncio
async def test_probe_reports_usage_exhaustion_with_the_selectors_reset_and_touches_no_state(db_setup) -> None:
    now_epoch = int(time.time())
    reset_at = now_epoch + 1800
    await _seed(
        [(_exhausted("acc_exhausted_only", now_epoch=now_epoch, reset_at=reset_at), 100.0, 40.0)],
        primary_reset_at=reset_at,
        secondary_reset_at=reset_at + 6 * 86400,
    )
    service = ProxyService(_repo_factory)
    balancer = service._load_balancer
    persisted_before = await _persisted_account_rows()

    first = await _probe_leaving_runtime_untouched(service, model=_MODEL)

    assert first is not None
    assert first.resets_at == reset_at
    assert first.selection.account is None
    assert first.selection.error_code == USAGE_LIMIT_REACHED
    assert first.selection.resets_at == reset_at

    # Read-only: a cold balancer stays cold (no runtime entry, lease or selection
    # mark), a second probe answers identically, and nothing was persisted.
    assert balancer._runtime == {}
    second = await _probe_leaving_runtime_untouched(service, model=_MODEL)
    assert second == first
    assert await _persisted_account_rows() == persisted_before
    assert await _sticky_row_count() == 0

    # Parity: the request's own selection answers the same structured 429.
    selection = await _foreground_selection(service, model=_MODEL)
    assert selection.account is None
    assert selection.error_code == USAGE_LIMIT_REACHED
    assert selection.resets_at == first.resets_at


@pytest.mark.asyncio
async def test_probe_reports_a_healthy_pool_as_not_exhausted_and_acquires_no_lease(db_setup) -> None:
    now_epoch = int(time.time())
    await _seed(
        [(_account("acc_healthy"), 20.0, 30.0)],
        primary_reset_at=now_epoch + 1800,
        secondary_reset_at=now_epoch + 6 * 86400,
    )
    service = ProxyService(_repo_factory)
    balancer = service._load_balancer

    assert await _probe_leaving_runtime_untouched(service, model=_MODEL) is None
    assert balancer._runtime == {}
    admitted = await _foreground_selection(service, model=_MODEL)
    assert admitted.account is not None

    # Contrast: ordinary selection marks the runtime, and a stream selection leaves a lease behind.
    assert balancer._runtime[admitted.account.id].last_selected_at is not None
    leased = await balancer.select_account(lease_kind="stream")
    assert leased.account is not None and leased.lease is not None
    assert balancer._runtime[leased.account.id].inflight_streams == 1
    await balancer.release_account_lease(leased.lease)


@pytest.mark.asyncio
async def test_service_tier_flips_the_answer_exactly_when_tier_filtering_removes_the_last_healthy_account(
    db_setup, monkeypatch
) -> None:
    now_epoch = int(time.time())
    pro_reset = now_epoch + 1800
    await _seed(
        [
            (_exhausted("acc_pro_exhausted", now_epoch=now_epoch, reset_at=pro_reset, plan_type="pro"), 100.0, 40.0),
            (_account("acc_plus_healthy", plan_type="plus"), 20.0, 30.0),
        ],
        primary_reset_at=pro_reset,
        secondary_reset_at=pro_reset + 6 * 86400,
    )
    # The model serves plus and pro accounts, but its priority tier only pro ones.
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_model_registry",
        lambda: SimpleNamespace(
            plan_types_for_model=lambda _model: frozenset({"plus", "pro"}),
            account_ids_for_model_service_tier=lambda _model, _tier: None,
            plan_types_for_model_service_tier=lambda _model, tier: (
                frozenset({"pro"}) if tier == "priority" else frozenset({"plus", "pro"})
            ),
        ),
    )
    service = ProxyService(_repo_factory)

    # Default tier: the healthy plus account keeps the pool usable.
    assert await _probe_leaving_runtime_untouched(service, model=_MODEL) is None
    assert await _probe_leaving_runtime_untouched(service, model=_MODEL, service_tier="default") is None
    assert (await _foreground_selection(service, model=_MODEL)).account is not None

    # Priority tier: filtering leaves only the exhausted pro account.
    priority = await _probe_leaving_runtime_untouched(service, model=_MODEL, service_tier="priority")
    assert priority is not None
    assert priority.resets_at == pro_reset
    priority_selection = await _foreground_selection(service, model=_MODEL, service_tier="priority")
    assert priority_selection.account is None
    assert priority_selection.error_code == USAGE_LIMIT_REACHED
    assert priority_selection.resets_at == pro_reset

    # No model: every plan is in scope, so the pool is exhausted only once every account is.
    assert await _probe_leaving_runtime_untouched(service, model=None) is None
    plus_reset = now_epoch + 900
    await _exhaust_persisted("acc_plus_healthy", now_epoch=now_epoch, reset_at=plus_reset)
    everything = await _probe_leaving_runtime_untouched(service, model=None)
    assert everything is not None
    assert everything.resets_at == plus_reset
    everything_selection = await _foreground_selection(service, model=None)
    assert everything_selection.error_code == USAGE_LIMIT_REACHED
    assert everything_selection.resets_at == plus_reset


@pytest.mark.asyncio
async def test_fresh_rate_limit_without_usage_evidence_is_not_exhaustion(db_setup) -> None:
    now_epoch = int(time.time())
    # A 429 just landed (status + block marker + reset) but no usage window proves 100 %.
    fresh_429 = _account(
        "acc_fresh_429",
        status=AccountStatus.RATE_LIMITED,
        reset_at=now_epoch + 300,
        blocked_at=now_epoch,
    )
    await _seed(
        [(fresh_429, 40.0, 30.0)],
        primary_reset_at=now_epoch + 300,
        secondary_reset_at=now_epoch + 6 * 86400,
    )
    service = ProxyService(_repo_factory)

    assert await _probe_leaving_runtime_untouched(service, model=_MODEL) is None

    selection = await _foreground_selection(service, model=_MODEL)
    assert selection.account is None
    assert selection.error_code != USAGE_LIMIT_REACHED
    status, envelope = selection_failure_response(selection)
    assert status == 503
    assert envelope["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_drain_strategies_decline_the_probe_even_when_the_pool_is_exhausted(db_setup) -> None:
    """Design decision 28: probe/foreground parity is established only outside the drain family, so under
    ``sequential_drain`` / ``reset_drain`` / ``single_account`` the probe declines (not exhausted) for a pool that
    the same probe reports exhausted under the default strategy, and it still touches no runtime state."""

    now_epoch = int(time.time())
    reset_at = now_epoch + 1800
    await _seed(
        [(_exhausted("acc_exhausted_drain", now_epoch=now_epoch, reset_at=reset_at), 100.0, 40.0)],
        primary_reset_at=reset_at,
        secondary_reset_at=reset_at + 6 * 86400,
    )
    service = ProxyService(_repo_factory)

    parity = await _probe_leaving_runtime_untouched(service, model=_MODEL)
    assert parity is not None and parity.resets_at == reset_at

    for routing_strategy in ("sequential_drain", "reset_drain", "single_account"):
        drain_settings = SimpleNamespace(routing_strategy=routing_strategy, single_account_id="acc_exhausted_drain")
        assert await _probe_leaving_runtime_untouched(service, model=_MODEL, settings=drain_settings) is None
    assert service._load_balancer._runtime == {}
