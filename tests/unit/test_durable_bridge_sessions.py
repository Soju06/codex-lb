from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.utils.time import utcnow
from app.db.models import Base
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator

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
        draining=True,
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
