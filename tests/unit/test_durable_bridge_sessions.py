from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clients.proxy import ProxyResponseError
from app.core.utils.time import utcnow
from app.db.models import (
    Base,
    HttpBridgeSessionAlias,
    HttpBridgeSessionRecord,
    HttpBridgeSessionState,
    StickySession,
    StickySessionKind,
)
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository

pytestmark = pytest.mark.unit


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


@pytest.mark.asyncio
async def test_durable_bridge_lookup_prefers_turn_state_then_previous_response_then_session_header(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id="key-1",
        instance_id="instance-a",
        lease_ttl_seconds=120.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_session_header(
        session_id=claimed.session_id,
        api_key_id="key-1",
        session_header="sid-123",
    )
    await coordinator.register_turn_state(
        session_id=claimed.session_id,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        turn_state="http_turn_1",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=claimed.session_id,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        response_id="resp_1",
        lease_ttl_seconds=120.0,
    )

    by_turn = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-1",
        api_key_id="key-1",
        turn_state="http_turn_1",
        session_header="sid-other",
        previous_response_id="resp_other",
    )
    assert by_turn is not None
    assert by_turn.canonical_kind == "session_header"
    assert by_turn.canonical_key == "sid-123"

    by_previous = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-1",
        api_key_id="key-1",
        turn_state=None,
        session_header="sid-other",
        previous_response_id="resp_1",
    )
    assert by_previous is not None
    assert by_previous.canonical_key == "sid-123"

    by_session = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-1",
        api_key_id="key-1",
        turn_state=None,
        session_header="sid-123",
        previous_response_id=None,
    )
    assert by_session is not None
    assert by_session.canonical_key == "sid-123"


@pytest.mark.asyncio
async def test_durable_bridge_lookup_rejects_conflicting_turn_and_response_aliases(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    turn_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-turn-owner",
        api_key_id="key-conflict",
        instance_id="instance-a",
        lease_ttl_seconds=120.0,
        account_id="acc-turn-owner",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    response_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-response-owner",
        api_key_id="key-conflict",
        instance_id="instance-b",
        lease_ttl_seconds=120.0,
        account_id="acc-response-owner",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=turn_owner.session_id,
        api_key_id="key-conflict",
        instance_id="instance-a",
        owner_epoch=turn_owner.owner_epoch,
        turn_state="http_turn_conflicting_owner",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=response_owner.session_id,
        api_key_id="key-conflict",
        instance_id="instance-b",
        owner_epoch=response_owner.owner_epoch,
        response_id="resp_conflicting_owner",
        lease_ttl_seconds=120.0,
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await coordinator.lookup_request_targets(
            session_key_kind="request",
            session_key_value="req-conflicting-owner",
            api_key_id="key-conflict",
            turn_state="http_turn_conflicting_owner",
            session_header=None,
            previous_response_id="resp_conflicting_owner",
        )

    assert exc_info.value.payload["error"]["code"] == "continuity_owner_conflict"


@pytest.mark.asyncio
async def test_durable_bridge_turn_state_lookup_does_not_fall_back_to_canonical_session_key(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=120.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=claimed.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        turn_state="http_turn_registered",
        lease_ttl_seconds=120.0,
    )

    registered = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_registered",
        api_key_id=None,
    )
    unknown = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_generated",
        api_key_id=None,
    )

    assert registered is not None
    assert registered.canonical_kind == "session_header"
    assert registered.canonical_key == "sid-123"
    assert unknown is None


@pytest.mark.asyncio
async def test_durable_bridge_turn_state_proof_does_not_accept_latest_state_without_alias(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-latest-only",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=120.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_latest_only",
        latest_response_id=None,
        allow_takeover=True,
    )

    assert (
        await coordinator.lookup_turn_state_target(
            turn_state="http_turn_latest_only",
            api_key_id=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_durable_bridge_stale_owner_cannot_register_turn_state_after_epoch_advance(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-stale-alias",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=120.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    replaced = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-stale-alias",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=120.0,
        account_id="acc-2",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    stale_registered = await coordinator.register_turn_state(
        session_id=claimed.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        turn_state="http_turn_stale_owner",
        lease_ttl_seconds=120.0,
    )
    current_registered = await coordinator.register_turn_state(
        session_id=replaced.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=replaced.owner_epoch,
        turn_state="http_turn_current_owner",
        lease_ttl_seconds=120.0,
    )

    assert stale_registered is False
    assert current_registered is True
    assert await coordinator.lookup_turn_state_target(turn_state="http_turn_stale_owner", api_key_id=None) is None
    assert await coordinator.lookup_turn_state_target(turn_state="http_turn_current_owner", api_key_id=None) is not None


@pytest.mark.asyncio
async def test_durable_bridge_claim_renews_same_owner_epoch(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    renewed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=True,
    )

    assert renewed.session_id == claimed.session_id
    assert renewed.owner_epoch == claimed.owner_epoch
    assert renewed.latest_turn_state == "http_turn_2"
    assert renewed.latest_response_id == "resp_2"


@pytest.mark.asyncio
async def test_durable_bridge_account_change_advances_epoch_to_fence_stale_release(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-account-change",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    replaced = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-account-change",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-2",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=False,
    )

    stale_release = await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=False,
    )

    assert replaced.session_id == claimed.session_id
    assert replaced.owner_instance_id == "instance-a"
    assert replaced.owner_epoch == claimed.owner_epoch + 1
    assert replaced.account_id == "acc-2"
    assert stale_release is not None
    assert stale_release.owner_instance_id == "instance-a"
    assert stale_release.owner_epoch == replaced.owner_epoch
    assert stale_release.state == "active"


@pytest.mark.asyncio
async def test_durable_bridge_forced_generation_advance_fences_same_account_stale_release(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-forced-generation",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    replaced = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-forced-generation",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=True,
        force_owner_epoch_advance=True,
    )

    stale_release = await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=False,
    )

    assert replaced.session_id == claimed.session_id
    assert replaced.owner_instance_id == "instance-a"
    assert replaced.owner_epoch == claimed.owner_epoch + 1
    assert stale_release is not None
    assert stale_release.owner_instance_id == "instance-a"
    assert stale_release.owner_epoch == replaced.owner_epoch
    assert stale_release.state == "active"


@pytest.mark.asyncio
async def test_durable_bridge_claim_takes_over_after_release(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id="resp_1",
        allow_takeover=True,
    )
    await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=True,
    )

    taken_over = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id=None,
        instance_id="instance-b",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=True,
    )

    assert taken_over.session_id == claimed.session_id
    assert taken_over.owner_instance_id == "instance-b"
    assert taken_over.owner_epoch == claimed.owner_epoch + 1
    assert taken_over.latest_response_id == "resp_2"


@pytest.mark.asyncio
async def test_durable_bridge_release_without_draining_marks_session_closed(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-closed",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    released = await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=False,
    )

    assert released is not None
    assert released.state == "closed"
    assert released.owner_instance_id is None

    reclaimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-closed",
        api_key_id=None,
        instance_id="instance-b",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=True,
    )

    assert reclaimed.owner_instance_id == "instance-b"
    assert reclaimed.latest_response_id == "resp_2"


@pytest.mark.asyncio
async def test_durable_bridge_takeover_clears_stale_recovery_anchor_for_fresh_session(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-reset",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_old",
        latest_response_id="resp_old",
        allow_takeover=True,
    )
    await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=False,
    )

    reclaimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-reset",
        api_key_id=None,
        instance_id="instance-b",
        lease_ttl_seconds=60.0,
        account_id="acc-2",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert reclaimed.owner_instance_id == "instance-b"
    assert reclaimed.latest_turn_state is None
    assert reclaimed.latest_response_id is None


@pytest.mark.asyncio
async def test_durable_bridge_same_account_closed_takeover_preserves_restart_anchor(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-restart",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_old",
        latest_response_id="resp_old",
        allow_takeover=True,
    )
    await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=False,
    )

    reclaimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-restart",
        api_key_id=None,
        instance_id="instance-b",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert reclaimed.owner_instance_id == "instance-b"
    assert reclaimed.latest_turn_state == "http_turn_old"
    assert reclaimed.latest_response_id == "resp_old"


@pytest.mark.asyncio
async def test_durable_bridge_takeover_preserves_existing_anchor_when_replacement_has_none(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-preserve",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_old",
        latest_response_id="resp_old",
        allow_takeover=True,
    )
    await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=True,
    )

    reclaimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-preserve",
        api_key_id=None,
        instance_id="instance-b",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert reclaimed.owner_instance_id == "instance-b"
    assert reclaimed.latest_turn_state == "http_turn_old"
    assert reclaimed.latest_response_id == "resp_old"


@pytest.mark.asyncio
async def test_durable_bridge_previous_response_records_completed_input_prefix(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-prefix",
        api_key_id="key-1",
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_prefix",
        latest_response_id=None,
        allow_takeover=True,
    )

    await coordinator.register_previous_response_id(
        session_id=claimed.session_id,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        response_id="resp_prefix",
        lease_ttl_seconds=60.0,
        input_item_count=3,
        input_full_fingerprint="a" * 64,
    )

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-prefix",
        api_key_id="key-1",
        turn_state=None,
        session_header="sid-prefix",
        previous_response_id=None,
    )

    assert lookup is not None
    assert lookup.latest_response_id == "resp_prefix"
    assert lookup.latest_input_item_count == 3
    assert lookup.latest_input_full_fingerprint == "a" * 64


@pytest.mark.asyncio
async def test_durable_bridge_takeover_with_account_change_clears_stale_aliases(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-alias-reset",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_old",
        latest_response_id="resp_old",
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=claimed.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        turn_state="http_turn_old",
        lease_ttl_seconds=60.0,
    )
    await coordinator.register_previous_response_id(
        session_id=claimed.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        response_id="resp_old",
        lease_ttl_seconds=60.0,
    )
    await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=True,
    )

    reclaimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-alias-reset",
        api_key_id=None,
        instance_id="instance-b",
        lease_ttl_seconds=60.0,
        account_id="acc-2",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert reclaimed.owner_instance_id == "instance-b"
    assert reclaimed.latest_turn_state is None
    assert reclaimed.latest_response_id is None

    stale_by_turn_state = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-1",
        api_key_id=None,
        turn_state="http_turn_old",
        session_header=None,
        previous_response_id=None,
    )
    stale_by_previous_response = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-1",
        api_key_id=None,
        turn_state=None,
        session_header=None,
        previous_response_id="resp_old",
    )
    by_canonical_key = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-alias-reset",
        api_key_id=None,
        turn_state=None,
        session_header=None,
        previous_response_id=None,
    )

    assert stale_by_turn_state is None
    assert stale_by_previous_response is None
    assert by_canonical_key is not None
    assert by_canonical_key.account_id == "acc-2"


@pytest.mark.asyncio
async def test_durable_bridge_lookup_active_lease_survives_request_lookup(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_1",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )
    assert claimed.lease_expires_at is not None
    assert claimed.lease_expires_at > utcnow() - timedelta(seconds=1)

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_1",
        api_key_id=None,
        turn_state=None,
        session_header=None,
        previous_response_id=None,
    )

    assert lookup is not None
    assert lookup.owner_instance_id == "instance-a"
    assert lookup.latest_response_id == "resp_1"
    assert lookup.lease_is_active(now=utcnow()) is True


@pytest.mark.asyncio
async def test_durable_bridge_lookup_falls_back_to_latest_turn_state_when_alias_missing(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="prompt_cache",
        session_key_value="thread-123",
        api_key_id="key-1",
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=claimed.session_id,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        turn_state="http_turn_restart",
        lease_ttl_seconds=60.0,
    )
    await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=True,
    )
    async with async_session_factory() as session:
        await session.execute(
            delete(HttpBridgeSessionAlias).where(
                HttpBridgeSessionAlias.session_id == claimed.session_id,
                HttpBridgeSessionAlias.alias_kind == "turn_state",
            )
        )
        await session.commit()

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_restart",
        api_key_id="key-1",
        turn_state="http_turn_restart",
        session_header=None,
        previous_response_id=None,
    )

    assert lookup is not None
    assert lookup.canonical_kind == "prompt_cache"
    assert lookup.canonical_key == "thread-123"
    assert lookup.state == "draining"


@pytest.mark.asyncio
async def test_durable_bridge_lookup_falls_back_to_latest_response_id_when_alias_missing(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="prompt_cache",
        session_key_value="thread-123",
        api_key_id="key-1",
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_previous_response_id(
        session_id=claimed.session_id,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        response_id="resp_restart",
        lease_ttl_seconds=60.0,
    )
    await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=True,
    )
    async with async_session_factory() as session:
        await session.execute(
            delete(HttpBridgeSessionAlias).where(
                HttpBridgeSessionAlias.session_id == claimed.session_id,
                HttpBridgeSessionAlias.alias_kind == "previous_response_id",
            )
        )
        await session.commit()

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-123",
        api_key_id="key-1",
        turn_state=None,
        session_header=None,
        previous_response_id="resp_restart",
    )

    assert lookup is not None
    assert lookup.canonical_kind == "prompt_cache"
    assert lookup.canonical_key == "thread-123"
    assert lookup.state == "draining"


@pytest.mark.asyncio
async def test_mark_instance_draining_keeps_current_owner_lease_active(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-draining",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    updated = await coordinator.mark_instance_draining(instance_id="instance-a")
    assert updated == 1

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-draining",
        api_key_id=None,
        turn_state=None,
        session_header="sid-draining",
        previous_response_id=None,
    )

    assert lookup is not None
    assert lookup.state == "draining"
    assert lookup.owner_instance_id == "instance-a"
    assert lookup.lease_expires_at == claimed.lease_expires_at
    assert lookup.lease_is_active(now=utcnow()) is True


@pytest.mark.asyncio
async def test_startup_purges_owned_bridge_rows(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        session.add(
            StickySession(
                key="parent-cache",
                kind=StickySessionKind.PROMPT_CACHE,
                account_id="acc-1",
            )
        )
        await session.commit()

    await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-restart",
        api_key_id=None,
        instance_id="instance-a",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    deleted = await coordinator.purge_owned_sessions_on_startup(
        instance_id="instance-a",
        ownerless_cutoff=utcnow() - timedelta(seconds=60),
    )

    assert deleted == 1
    assert (
        await coordinator.lookup_request_targets(
            session_key_kind="session_header",
            session_key_value="sid-restart",
            api_key_id=None,
            turn_state=None,
            session_header="sid-restart",
            previous_response_id=None,
        )
        is None
    )

    async with async_session_factory() as session:
        sticky = await session.get(
            StickySession,
            ("parent-cache", StickySessionKind.PROMPT_CACHE),
        )
        assert sticky is not None


@pytest.mark.asyncio
async def test_startup_purges_ownerless_stale_rows(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    stale_time = utcnow() - timedelta(seconds=120)

    async with async_session_factory() as session:
        session.add(
            HttpBridgeSessionRecord(
                session_key_kind="session_header",
                session_key_value="sid-stale",
                session_key_hash="hash-stale",
                api_key_scope="__anonymous__",
                owner_instance_id=None,
                owner_epoch=1,
                lease_expires_at=stale_time,
                state=HttpBridgeSessionState.ACTIVE,
                account_id="acc-1",
                model="gpt-5.4",
                last_seen_at=stale_time,
                closed_at=None,
            )
        )
        await session.commit()

    deleted = await coordinator.purge_owned_sessions_on_startup(
        instance_id="instance-a",
        ownerless_cutoff=utcnow() - timedelta(seconds=60),
    )

    assert deleted == 1
    assert (
        await coordinator.lookup_request_targets(
            session_key_kind="session_header",
            session_key_value="sid-stale",
            api_key_id=None,
            turn_state=None,
            session_header="sid-stale",
            previous_response_id=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_startup_preserves_ownerless_rows_without_retention_cutoff(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    stale_time = utcnow() - timedelta(hours=12)

    async with async_session_factory() as session:
        session.add(
            HttpBridgeSessionRecord(
                session_key_kind="session_header",
                session_key_value="sid-ownerless-default",
                session_key_hash="hash-ownerless-default",
                api_key_scope="__anonymous__",
                owner_instance_id=None,
                owner_epoch=1,
                lease_expires_at=stale_time,
                state=HttpBridgeSessionState.ACTIVE,
                account_id="acc-1",
                model="gpt-5.4",
                last_seen_at=stale_time,
                closed_at=None,
            )
        )
        await session.commit()

    deleted = await coordinator.purge_owned_sessions_on_startup(instance_id="instance-a")

    assert deleted == 0
    async with async_session_factory() as session:
        row = await session.scalar(
            select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.session_key_value == "sid-ownerless-default")
        )
    assert row is not None


@pytest.mark.asyncio
async def test_startup_purge_batches_owned_rows(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    old_time = utcnow() - timedelta(minutes=5)

    async with async_session_factory() as session:
        for index in range(3):
            session_id = f"sid-owned-batch-{index}"
            session.add(
                HttpBridgeSessionRecord(
                    id=session_id,
                    session_key_kind="session_header",
                    session_key_value=session_id,
                    session_key_hash=f"hash-owned-batch-{index}",
                    api_key_scope="__anonymous__",
                    owner_instance_id="instance-a",
                    owner_epoch=1,
                    lease_expires_at=old_time,
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id="acc-1",
                    model="gpt-5.4",
                    last_seen_at=old_time,
                    closed_at=None,
                )
            )
            session.add(
                HttpBridgeSessionAlias(
                    session_id=session_id,
                    alias_kind="session_header",
                    alias_value=session_id,
                    alias_hash=f"alias-owned-batch-{index}",
                    api_key_scope="__anonymous__",
                )
            )
        await session.commit()

        repo = DurableBridgeRepository(session)
        deleted = await repo.purge_owned_sessions_on_startup(instance_id="instance-a", batch_size=2)

        assert deleted == 3
        remaining = await session.execute(select(HttpBridgeSessionRecord.id))
        assert remaining.scalars().all() == []
        remaining_aliases = await session.execute(select(HttpBridgeSessionAlias.session_id))
        assert remaining_aliases.scalars().all() == []


@pytest.mark.asyncio
async def test_startup_preserves_recent_ownerless_drain_rows(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-fresh-drain",
        api_key_id=None,
        instance_id="instance-draining",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_drain",
        latest_response_id="resp_drain",
        allow_takeover=True,
    )
    await coordinator.register_session_header(
        session_id=claimed.session_id,
        api_key_id=None,
        session_header="sid-fresh-drain",
    )
    released = await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-draining",
        owner_epoch=claimed.owner_epoch,
        draining=True,
    )

    assert released is not None
    assert released.owner_instance_id is None
    assert released.state == HttpBridgeSessionState.DRAINING

    deleted = await coordinator.purge_owned_sessions_on_startup(instance_id="instance-a")

    assert deleted == 0
    lookup = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-fresh-drain",
        api_key_id=None,
        turn_state=None,
        session_header="sid-fresh-drain",
        previous_response_id=None,
    )
    assert lookup is not None
    assert lookup.session_id == claimed.session_id
    assert lookup.state == HttpBridgeSessionState.DRAINING


@pytest.mark.asyncio
async def test_startup_rechecks_ownerless_stale_rows_before_delete(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_time = utcnow() - timedelta(seconds=120)

    async with async_session_factory() as session:
        session.add(
            HttpBridgeSessionRecord(
                id="sid-race-claim",
                session_key_kind="session_header",
                session_key_value="sid-race-claim",
                session_key_hash="hash-race-claim",
                api_key_scope="__anonymous__",
                owner_instance_id=None,
                owner_epoch=1,
                lease_expires_at=stale_time,
                state=HttpBridgeSessionState.ACTIVE,
                account_id="acc-1",
                model="gpt-5.4",
                last_seen_at=stale_time,
                closed_at=None,
            )
        )
        session.add(
            HttpBridgeSessionAlias(
                session_id="sid-race-claim",
                alias_kind="session_header",
                alias_value="sid-race-claim",
                alias_hash="hash-race-claim-alias",
                api_key_scope="__anonymous__",
            )
        )
        await session.commit()

        repo = DurableBridgeRepository(session)
        original_execute = session.execute
        selected_for_purge = False

        async def execute_and_claim_after_candidate_select(statement, *args, **kwargs):
            nonlocal selected_for_purge
            result = await original_execute(statement, *args, **kwargs)
            if not selected_for_purge and statement.is_select:
                selected_for_purge = True
                await original_execute(
                    update(HttpBridgeSessionRecord)
                    .where(HttpBridgeSessionRecord.id == "sid-race-claim")
                    .values(
                        owner_instance_id="instance-b",
                        owner_epoch=2,
                        lease_expires_at=utcnow() + timedelta(seconds=60),
                        last_seen_at=utcnow(),
                    )
                )
                await session.commit()
            return result

        monkeypatch.setattr(session, "execute", execute_and_claim_after_candidate_select)

        deleted = await repo.purge_owned_sessions_on_startup(instance_id="instance-a")

        assert deleted == 0
        row = await session.get(HttpBridgeSessionRecord, "sid-race-claim", populate_existing=True)
        assert row is not None
        assert row.owner_instance_id == "instance-b"
        aliases = await session.execute(
            select(HttpBridgeSessionAlias).where(HttpBridgeSessionAlias.session_id == "sid-race-claim")
        )
        assert aliases.scalar_one_or_none() is not None
