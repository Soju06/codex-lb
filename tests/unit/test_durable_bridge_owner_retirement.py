from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import (
    Account,
    AccountStatus,
    Base,
    HttpBridgeOperationRecord,
    HttpBridgeSessionAlias,
    HttpBridgeSessionRecord,
)
from app.modules.proxy.account_eligibility import HARD_OWNER_UNAVAILABLE_STATUSES
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository

pytestmark = pytest.mark.unit

_GRACE = timedelta(hours=6)


@pytest.fixture
async def async_session_factory() -> AsyncIterator[Callable[[], AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    def get_session() -> AsyncSession:
        return session_maker()

    yield get_session
    await engine.dispose()


@pytest.fixture
async def coordinator(async_session_factory: Callable[[], AsyncSession]) -> DurableBridgeSessionCoordinator:
    return DurableBridgeSessionCoordinator(async_session_factory)


async def _add_account(
    factory: Callable[[], AsyncSession],
    account_id: str,
    status: AccountStatus,
    *,
    reset_at: int | None = None,
) -> None:
    async with factory() as session:
        session.add(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="pro",
                access_token_encrypted=b"a",
                refresh_token_encrypted=b"r",
                id_token_encrypted=b"i",
                last_refresh=to_utc_naive(utcnow()),
                status=status,
                reset_at=reset_at,
                codex_installation_id="install-1",
            )
        )
        await session.commit()


async def _claim(
    coordinator: DurableBridgeSessionCoordinator,
    *,
    account_id: str,
    key: str = "thread-1",
    latest_response_id: str | None = "resp_1",
) -> str:
    claimed = await coordinator.claim_live_session(
        session_key_kind="thread_header",
        session_key_value=key,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id=account_id,
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=latest_response_id,
        allow_takeover=True,
    )
    return claimed.session_id


async def _age_row(factory: Callable[[], AsyncSession], *, seconds: float) -> None:
    stale = to_utc_naive(utcnow() - timedelta(seconds=seconds))
    async with factory() as session:
        await session.execute(update(HttpBridgeSessionRecord).values(last_seen_at=stale))
        await session.commit()


async def _lookup(coordinator: DurableBridgeSessionCoordinator, key: str = "thread-1"):
    return await coordinator.lookup_request_targets(
        session_key_kind="thread_header",
        session_key_value=key,
        api_key_id="key-1",
        turn_state=None,
        session_header=None,
        previous_response_id=None,
    )


async def _retire(factory: Callable[[], AsyncSession], *, grace: timedelta = _GRACE) -> int:
    now = utcnow()
    async with factory() as session:
        return await DurableBridgeRepository(session).retire_stale_unavailable_bridge_owners(
            now - grace,
            now=now,
        )


async def test_unroutable_owner_past_the_grace_window_is_retired(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 1

    lookup = await _lookup(coordinator)
    assert lookup is not None
    # The row still identifies the thread, but names no owner and no anchor:
    # an anchor without its account is state no replacement can read.
    assert lookup.continuity_abandoned is True
    assert lookup.account_id is None
    assert lookup.latest_response_id is None
    assert lookup.latest_turn_state is None
    assert lookup.retired_account_id == "acc-paused"
    assert lookup.session_id is not None


async def test_recent_activity_keeps_an_unroutable_owner(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() - 600)

    assert await _retire(async_session_factory) == 0

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.continuity_abandoned is False
    assert lookup.account_id == "acc-paused"


async def test_a_dormant_thread_is_retired_on_a_fresh_outage(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """The window protects a live thread, not a young outage.

    The sticky sweep needs an outage clock because StickySession has no
    activity signal — its timestamp moves on every pin refresh, so it cannot
    tell "owner died long ago" from "owner died just now". A bridge row records
    when the thread last actually served, which answers the question the grace
    is really asking: is this thread live enough for its anchor to be worth
    protecting? An anchor nobody has touched in six hours is worth less than
    the thread being resumable at all, so a dormant thread is retired as soon
    as its owner is unroutable.
    """
    await _add_account(async_session_factory, "acc-fresh-outage", AccountStatus.ACTIVE)
    await _claim(coordinator, account_id="acc-fresh-outage")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    # Healthy owner: idleness alone never retires.
    assert await _retire(async_session_factory) == 0

    # The outage starts now, long after the thread went quiet.
    async with async_session_factory() as session:
        await session.execute(update(Account).values(status=AccountStatus.PAUSED))
        await session.commit()

    assert await _retire(async_session_factory) == 1


async def test_healthy_owner_is_never_retired(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-active", AccountStatus.ACTIVE)
    await _claim(coordinator, account_id="acc-active")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() * 10)

    assert await _retire(async_session_factory) == 0

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.account_id == "acc-active"


async def test_rate_limited_owner_with_a_future_reset_is_not_retired(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    # A reset horizon is evidence the owner comes back, so waiting preserves
    # the upstream prompt cache instead of forcing a full resend.
    future_reset = naive_utc_to_epoch(to_utc_naive(utcnow() + timedelta(hours=2)))
    await _add_account(
        async_session_factory,
        "acc-limited",
        AccountStatus.RATE_LIMITED,
        reset_at=future_reset,
    )
    await _claim(coordinator, account_id="acc-limited")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 0

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.account_id == "acc-limited"


async def test_rate_limited_owner_past_its_reset_is_retired(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    past_reset = naive_utc_to_epoch(to_utc_naive(utcnow() - timedelta(hours=2)))
    await _add_account(
        async_session_factory,
        "acc-limited",
        AccountStatus.RATE_LIMITED,
        reset_at=past_reset,
    )
    await _claim(coordinator, account_id="acc-limited")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 1


async def test_reauth_required_owner_is_retired_after_the_grace_window(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    # The single largest source of this failure in production, and the one
    # status that neither the DEACTIVATED cleanup nor the sticky grace covers.
    await _add_account(async_session_factory, "acc-reauth", AccountStatus.REAUTH_REQUIRED)
    await _claim(coordinator, account_id="acc-reauth")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 1


async def test_retirement_keeps_operation_rows(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """The ~exists(operation) guard must not block phase 1.

    Every row poisoned in the 2026-09-04 outage owned operations, so gating
    retirement on that guard would reproduce it exactly.
    """
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    session_id = await _claim(coordinator, account_id="acc-paused")
    async with async_session_factory() as session:
        session.add(
            HttpBridgeOperationRecord(
                operation_id="op-1",
                session_id=session_id,
                request_fingerprint="fp-1",
                state="completed",
            )
        )
        await session.commit()
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 1

    async with async_session_factory() as session:
        operations = (await session.execute(select(HttpBridgeOperationRecord))).scalars().all()
        rows = (await session.execute(select(HttpBridgeSessionRecord))).scalars().all()
    assert len(operations) == 1
    assert len(rows) == 1


async def test_expired_tombstone_without_operations_is_deleted(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    assert await _retire(async_session_factory) == 1

    # Age the tombstone itself past a second full window.
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord).values(
                continuity_abandoned_at=to_utc_naive(utcnow() - _GRACE - timedelta(minutes=1))
            )
        )
        await session.commit()

    assert await _retire(async_session_factory) == 1

    async with async_session_factory() as session:
        rows = (await session.execute(select(HttpBridgeSessionRecord))).scalars().all()
        aliases = (await session.execute(select(HttpBridgeSessionAlias))).scalars().all()
    assert rows == []
    assert aliases == []


async def test_expired_tombstone_with_operations_is_kept(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """Phase 2 keeps the guard: deleting the row would cascade the ledger."""
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    session_id = await _claim(coordinator, account_id="acc-paused")
    async with async_session_factory() as session:
        session.add(
            HttpBridgeOperationRecord(
                operation_id="op-1",
                session_id=session_id,
                request_fingerprint="fp-1",
                state="completed",
            )
        )
        await session.commit()
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    assert await _retire(async_session_factory) == 1

    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord).values(
                continuity_abandoned_at=to_utc_naive(utcnow() - _GRACE - timedelta(minutes=1))
            )
        )
        await session.commit()

    assert await _retire(async_session_factory) == 0

    async with async_session_factory() as session:
        rows = (await session.execute(select(HttpBridgeSessionRecord))).scalars().all()
    assert len(rows) == 1


async def test_retirement_is_idempotent(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 1
    assert await _retire(async_session_factory) == 0


async def test_a_fresh_claim_clears_the_retirement(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """Retirement is reversible, exactly as a sticky rebind clears its marker."""
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _add_account(async_session_factory, "acc-healthy", AccountStatus.ACTIVE)
    await _claim(coordinator, account_id="acc-paused")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    assert await _retire(async_session_factory) == 1

    await _claim(coordinator, account_id="acc-healthy", latest_response_id="resp_2")

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.continuity_abandoned is False
    assert lookup.account_id == "acc-healthy"
    assert lookup.retired_account_id is None


async def test_ownerless_rows_are_left_to_the_existing_purges(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused")
    async with async_session_factory() as session:
        await session.execute(update(HttpBridgeSessionRecord).values(account_id=None))
        await session.commit()
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 0


async def test_a_retired_row_names_no_owning_replica(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """Retirement must not depend on the lease TTL staying below the grace window.

    ``_durable_bridge_lookup_active_owner`` reads the owner instance and lease
    off the lookup, so a retired row that still carried them would keep being
    forwarded to the replica that owned it.
    """
    from app.modules.proxy._service.http_bridge.helpers import _durable_bridge_lookup_active_owner

    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    # A lease far in the future, which the sweep does not clear on the row.
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord).values(lease_expires_at=to_utc_naive(utcnow() + timedelta(hours=12)))
        )
        await session.commit()

    assert await _retire(async_session_factory) == 1

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.owner_instance_id is None
    assert lookup.lease_expires_at is None
    assert lookup.lease_is_active(now=utcnow()) is False
    assert _durable_bridge_lookup_active_owner(lookup) is None
    # Fencing state survives: a later claim still has to advance past it.
    assert lookup.owner_epoch >= 1


async def test_reclaiming_the_recovered_former_owner_discards_the_abandoned_anchor(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """Clearing the marker must not resurrect the anchor it was masking.

    When the retired account itself recovers and is selected again,
    ``account_changed`` is False, so a claim that cleared only the marker would
    leave the old response id and fingerprints behind — and the next lookup
    would serve that abandoned anchor as ordinary continuity, even though the
    request that triggered the claim was planned as an unanchored fresh start.
    """
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused", latest_response_id="resp_stale")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    assert await _retire(async_session_factory) == 1

    # Same account, recovered, with no anchor supplied by the fresh request.
    async with async_session_factory() as session:
        await session.execute(update(Account).values(status=AccountStatus.ACTIVE))
        await session.commit()
    await _claim(coordinator, account_id="acc-paused", latest_response_id=None)

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.continuity_abandoned is False
    assert lookup.account_id == "acc-paused"
    assert lookup.latest_response_id is None
    assert lookup.latest_turn_state is None
    assert lookup.latest_input_item_count is None
    assert lookup.latest_input_full_fingerprint is None

    async with async_session_factory() as session:
        aliases = (await session.execute(select(HttpBridgeSessionAlias))).scalars().all()
    assert list(aliases) == []


async def test_a_retired_row_still_refuses_a_client_supplied_anchor(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """Retirement frees proxy-injected continuations, not client-supplied anchors.

    A client that sends its own ``previous_response_id`` names upstream state
    that lived on the retired account, and no replacement can read it. The
    request still fails closed — the same outcome as before this change, since
    the owner was unroutable either way. Turning that into an actionable,
    non-retryable error is the follow-up; this pins the boundary so the
    follow-up is a deliberate change rather than a surprise.
    """
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _claim(coordinator, account_id="acc-paused", latest_response_id="resp_anchor")
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    assert await _retire(async_session_factory) == 1

    by_anchor = await coordinator.lookup_request_targets(
        session_key_kind="thread_header",
        session_key_value="thread-1",
        api_key_id="key-1",
        turn_state=None,
        session_header=None,
        previous_response_id="resp_anchor",
    )
    assert by_anchor is not None
    # Resolvable as a row, but it names nobody who could honour the anchor.
    assert by_anchor.continuity_abandoned is True
    assert by_anchor.account_id is None
    assert by_anchor.latest_response_id is None


async def _retire_now(
    factory: Callable[[], AsyncSession],
    *,
    session_id: str,
    account_id: str,
    deadline_epoch: int,
) -> bool:
    async with factory() as session:
        return await DurableBridgeRepository(session).retire_continuity_owner_if_unavailable(
            session_id,
            expected_account_id=account_id,
            recovery_deadline_epoch=deadline_epoch,
        )


def _epoch_in(seconds: float) -> int:
    return naive_utc_to_epoch(to_utc_naive(utcnow() + timedelta(seconds=seconds)))


async def test_request_path_retires_an_owner_with_no_recovery_horizon(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """A paused owner carries no horizon, so waiting can never help."""
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    session_id = await _claim(coordinator, account_id="acc-paused")

    assert await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-paused",
        deadline_epoch=_epoch_in(7200),
    )

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.continuity_abandoned is True
    assert lookup.account_id is None
    # No grace elapsed and no sweep ran: the row was freed for the waiting turn.
    assert lookup.retired_account_id == "acc-paused"


async def test_request_path_waits_for_an_owner_returning_inside_the_budget(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """A reset inside the budget is evidence the owner returns, so keep the cache."""
    await _add_account(
        async_session_factory,
        "acc-limited",
        AccountStatus.RATE_LIMITED,
        reset_at=_epoch_in(60),
    )
    session_id = await _claim(coordinator, account_id="acc-limited")

    assert not await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-limited",
        deadline_epoch=_epoch_in(7200),
    )

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.account_id == "acc-limited"


async def test_request_path_retires_an_owner_returning_after_the_budget(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """A five-hour limit with hours left is not worth waiting out."""
    await _add_account(
        async_session_factory,
        "acc-limited",
        AccountStatus.RATE_LIMITED,
        reset_at=_epoch_in(4 * 3600),
    )
    session_id = await _claim(coordinator, account_id="acc-limited")

    assert await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-limited",
        deadline_epoch=_epoch_in(7200),
    )


async def test_request_path_never_retires_a_healthy_owner(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-active", AccountStatus.ACTIVE)
    session_id = await _claim(coordinator, account_id="acc-active")

    assert not await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-active",
        deadline_epoch=_epoch_in(7200),
    )


async def test_request_path_retirement_requires_the_expected_owner(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """A concurrent rebind must lose the race, not have its new owner retired."""
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _add_account(async_session_factory, "acc-other", AccountStatus.PAUSED)
    session_id = await _claim(coordinator, account_id="acc-paused")

    assert not await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-other",
        deadline_epoch=_epoch_in(7200),
    )

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.account_id == "acc-paused"


async def test_request_path_retirement_is_idempotent(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    session_id = await _claim(coordinator, account_id="acc-paused")

    assert await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-paused",
        deadline_epoch=_epoch_in(7200),
    )
    assert not await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-paused",
        deadline_epoch=_epoch_in(7200),
    )


async def test_request_path_retirement_is_cleared_by_a_fresh_claim(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """The scope-only marker is as reversible as the sweep's timestamp form."""
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    await _add_account(async_session_factory, "acc-healthy", AccountStatus.ACTIVE)
    session_id = await _claim(coordinator, account_id="acc-paused")
    assert await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-paused",
        deadline_epoch=_epoch_in(7200),
    )

    await _claim(coordinator, account_id="acc-healthy", latest_response_id=None)

    lookup = await _lookup(coordinator)
    assert lookup is not None
    assert lookup.continuity_abandoned is False
    assert lookup.account_id == "acc-healthy"


async def test_an_unclaimed_request_path_marker_is_collected(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """A request-path retirement must not leak when the rebind never lands.

    It writes the scope marker alone, which satisfies neither sweep phase on
    its own — phase 1 wants both columns NULL, phase 2 wants a timestamp — so
    the sweep promotes it to the global form once the row goes stale, and the
    next sweep collects it.
    """
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    session_id = await _claim(coordinator, account_id="acc-paused")
    assert await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-paused",
        deadline_epoch=_epoch_in(7200),
    )
    async with async_session_factory() as session:
        row = (await session.execute(select(HttpBridgeSessionRecord))).scalar_one()
        assert row.continuity_abandonment_scope == "request_path"
        assert row.continuity_abandoned_at is None

    # Still fresh: nothing to collect yet.
    assert await _retire(async_session_factory) == 0

    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)
    assert await _retire(async_session_factory) == 1
    async with async_session_factory() as session:
        row = (await session.execute(select(HttpBridgeSessionRecord))).scalar_one()
        assert row.continuity_abandonment_scope is None
        assert row.continuity_abandoned_at is not None

    # Aged past a second window, the promoted tombstone is deleted.
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord).values(
                continuity_abandoned_at=to_utc_naive(utcnow() - _GRACE - timedelta(minutes=1))
            )
        )
        await session.commit()
    assert await _retire(async_session_factory) == 1
    async with async_session_factory() as session:
        assert (await session.execute(select(HttpBridgeSessionRecord))).scalars().all() == []


async def test_promotion_does_not_need_the_owner_to_be_unavailable(
    async_session_factory: Callable[[], AsyncSession],
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """An already-retired row is collected even if its old owner recovered.

    Otherwise a marker written while the account was paused would outlive every
    sweep once the operator resumed it.
    """
    await _add_account(async_session_factory, "acc-paused", AccountStatus.PAUSED)
    session_id = await _claim(coordinator, account_id="acc-paused")
    assert await _retire_now(
        async_session_factory,
        session_id=session_id,
        account_id="acc-paused",
        deadline_epoch=_epoch_in(7200),
    )
    async with async_session_factory() as session:
        await session.execute(update(Account).values(status=AccountStatus.ACTIVE))
        await session.commit()
    await _age_row(async_session_factory, seconds=_GRACE.total_seconds() + 60)

    assert await _retire(async_session_factory) == 1


def test_unavailable_status_set_covers_every_non_serving_status() -> None:
    assert HARD_OWNER_UNAVAILABLE_STATUSES == {
        AccountStatus.PAUSED,
        AccountStatus.DEACTIVATED,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
        AccountStatus.REAUTH_REQUIRED,
    }
    assert AccountStatus.ACTIVE not in HARD_OWNER_UNAVAILABLE_STATUSES
