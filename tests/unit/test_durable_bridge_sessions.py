from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

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
from app.modules.proxy.continuity import (
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
    is_http_bridge_account_neutral_replay,
    make_http_bridge_account_neutral_replay_key,
)
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup, DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeAliasRegistration,
    DurableBridgeRepository,
    durable_bridge_hash,
    durable_bridge_operation_id,
)

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


def test_durable_bridge_live_claim_requires_process_epoch() -> None:
    parameter = inspect.signature(DurableBridgeSessionCoordinator.claim_live_session).parameters["owner_process_epoch"]

    assert parameter.default is inspect.Parameter.empty


@pytest.mark.asyncio
async def test_durable_bridge_lookup_prefers_turn_state_then_previous_response_then_session_header(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id="key-1",
        instance_id="instance-a",
        owner_process_epoch="test-process",
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
async def test_reversible_recovery_turn_state_registration_restores_previous_owner(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    predecessor = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-recovery-predecessor",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-a",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    assert (
        await coordinator.register_turn_state(
            session_id=predecessor.session_id,
            api_key_id=None,
            instance_id="instance-a",
            owner_epoch=predecessor.owner_epoch,
            turn_state="http_turn_reversible",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )

    recovery_kind, recovery_key = make_http_bridge_account_neutral_replay_key("reversible")
    recovery = await coordinator.claim_live_session(
        session_key_kind=recovery_kind,
        session_key_value=recovery_key,
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-b",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state="http_turn_recovery_previous",
        latest_response_id=None,
        allow_takeover=True,
    )

    receipt = await coordinator.register_recovery_turn_state(
        session_id=recovery.session_id,
        api_key_id=None,
        instance_id="instance-b",
        owner_epoch=recovery.owner_epoch,
        turn_state="http_turn_reversible",
        lease_ttl_seconds=120.0,
    )

    assert receipt.status == DurableBridgeAliasRegistration.REGISTERED
    rebound = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_reversible",
        api_key_id=None,
    )
    assert rebound is not None
    assert rebound.session_id == recovery.session_id
    assert rebound.latest_turn_state == "http_turn_reversible"

    rolled_back = await coordinator.rollback_recovery_turn_state_registration(
        receipt=receipt,
    )

    assert rolled_back is True
    restored = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_reversible",
        api_key_id=None,
    )
    assert restored is not None
    assert restored.session_id == predecessor.session_id
    recovery_after_rollback = await coordinator.lookup_sessions(session_ids=[recovery.session_id])
    assert recovery_after_rollback[0].latest_turn_state == "http_turn_recovery_previous"


@pytest.mark.asyncio
async def test_reversible_recovery_rollback_does_not_restore_reclaimed_predecessor(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    predecessor = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-reclaimed-predecessor",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-a",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    assert (
        await coordinator.register_turn_state(
            session_id=predecessor.session_id,
            api_key_id=None,
            instance_id="instance-a",
            owner_epoch=predecessor.owner_epoch,
            turn_state="http_turn_reclaimed",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )
    recovery_kind, recovery_key = make_http_bridge_account_neutral_replay_key("reclaimed")
    recovery = await coordinator.claim_live_session(
        session_key_kind=recovery_kind,
        session_key_value=recovery_key,
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-b",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    receipt = await coordinator.register_recovery_turn_state(
        session_id=recovery.session_id,
        api_key_id=None,
        instance_id="instance-b",
        owner_epoch=recovery.owner_epoch,
        turn_state="http_turn_reclaimed",
        lease_ttl_seconds=120.0,
    )
    assert receipt.status == DurableBridgeAliasRegistration.REGISTERED

    reclaimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-reclaimed-predecessor",
        api_key_id=None,
        instance_id="instance-c",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-c",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
        force_owner_epoch_advance=True,
    )
    assert reclaimed.owner_epoch > predecessor.owner_epoch
    assert reclaimed.account_id == "acc-c"

    assert await coordinator.rollback_recovery_turn_state_registration(receipt=receipt) is True
    assert (
        await coordinator.lookup_turn_state_target(
            turn_state="http_turn_reclaimed",
            api_key_id=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_durable_bridge_lookup_accepts_same_account_alias_session_divergence(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    turn_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-turn-owner",
        api_key_id="key-same-account",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-shared",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    response_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-response-owner",
        api_key_id="key-same-account",
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-shared",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=turn_owner.session_id,
        api_key_id="key-same-account",
        instance_id="instance-a",
        owner_epoch=turn_owner.owner_epoch,
        turn_state="http_turn_same_account_owner",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=response_owner.session_id,
        api_key_id="key-same-account",
        instance_id="instance-b",
        owner_epoch=response_owner.owner_epoch,
        response_id="resp_same_account_owner",
        lease_ttl_seconds=120.0,
    )
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == turn_owner.session_id)
            .values(last_seen_at=utcnow() + timedelta(seconds=10))
        )
        await session.commit()

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-same-account-owner",
        api_key_id="key-same-account",
        turn_state="http_turn_same_account_owner",
        session_header=None,
        previous_response_id="resp_same_account_owner",
    )

    assert lookup is not None
    assert lookup.session_id == response_owner.session_id
    assert lookup.account_id == "acc-shared"
    assert lookup.latest_response_id == "resp_same_account_owner"


@pytest.mark.asyncio
async def test_durable_bridge_lookup_prefers_newest_same_account_response_anchor(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    turn_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-turn-old-anchor",
        api_key_id="key-newest-anchor",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-shared",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    session_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-session-new-anchor",
        api_key_id="key-newest-anchor",
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-shared",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=turn_owner.session_id,
        api_key_id="key-newest-anchor",
        instance_id="instance-a",
        owner_epoch=turn_owner.owner_epoch,
        turn_state="turn-old-anchor",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=turn_owner.session_id,
        api_key_id="key-newest-anchor",
        instance_id="instance-a",
        owner_epoch=turn_owner.owner_epoch,
        response_id="resp-old-anchor",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_session_header(
        session_id=session_owner.session_id,
        api_key_id="key-newest-anchor",
        session_header="sid-session-new-anchor",
    )
    await coordinator.register_previous_response_id(
        session_id=session_owner.session_id,
        api_key_id="key-newest-anchor",
        instance_id="instance-b",
        owner_epoch=session_owner.owner_epoch,
        response_id="resp-new-anchor",
        lease_ttl_seconds=120.0,
    )
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == turn_owner.session_id)
            .values(last_seen_at=utcnow() - timedelta(seconds=10))
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == session_owner.session_id)
            .values(last_seen_at=utcnow())
        )
        await session.commit()

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-newest-anchor",
        api_key_id="key-newest-anchor",
        turn_state="turn-old-anchor",
        session_header="sid-session-new-anchor",
        previous_response_id=None,
    )

    assert lookup is not None
    assert lookup.session_id == session_owner.session_id
    assert lookup.latest_response_id == "resp-new-anchor"


@pytest.mark.asyncio
async def test_durable_bridge_lookup_preserves_requested_response_alias_after_anchor_advances(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    requested_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-requested-anchor",
        api_key_id="key-requested-anchor",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-shared",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    fresher_turn_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-fresher-turn",
        api_key_id="key-requested-anchor",
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-shared",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_previous_response_id(
        session_id=requested_owner.session_id,
        api_key_id="key-requested-anchor",
        instance_id="instance-a",
        owner_epoch=requested_owner.owner_epoch,
        response_id="resp-requested-old",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=requested_owner.session_id,
        api_key_id="key-requested-anchor",
        instance_id="instance-a",
        owner_epoch=requested_owner.owner_epoch,
        response_id="resp-requested-latest",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_turn_state(
        session_id=fresher_turn_owner.session_id,
        api_key_id="key-requested-anchor",
        instance_id="instance-b",
        owner_epoch=fresher_turn_owner.owner_epoch,
        turn_state="turn-fresher-than-requested",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=fresher_turn_owner.session_id,
        api_key_id="key-requested-anchor",
        instance_id="instance-b",
        owner_epoch=fresher_turn_owner.owner_epoch,
        response_id="resp-fresher-turn",
        lease_ttl_seconds=120.0,
    )
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == fresher_turn_owner.session_id)
            .values(last_seen_at=utcnow() + timedelta(seconds=10))
        )
        await session.commit()

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="request",
        session_key_value="req-requested-anchor",
        api_key_id="key-requested-anchor",
        turn_state="turn-fresher-than-requested",
        session_header=None,
        previous_response_id="resp-requested-old",
    )

    assert lookup is not None
    assert lookup.session_id == requested_owner.session_id
    assert lookup.latest_response_id == "resp-requested-latest"


@pytest.mark.asyncio
async def test_durable_bridge_lookup_rejects_ownerless_and_live_alias_divergence(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    ownerless = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-ownerless",
        api_key_id="key-ownerless-conflict",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id=None,
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    live_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-live-owner",
        api_key_id="key-ownerless-conflict",
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-live",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=ownerless.session_id,
        api_key_id="key-ownerless-conflict",
        instance_id="instance-a",
        owner_epoch=ownerless.owner_epoch,
        turn_state="http_turn_ownerless",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=live_owner.session_id,
        api_key_id="key-ownerless-conflict",
        instance_id="instance-b",
        owner_epoch=live_owner.owner_epoch,
        response_id="resp_live_owner",
        lease_ttl_seconds=120.0,
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await coordinator.lookup_request_targets(
            session_key_kind="request",
            session_key_value="req-ownerless-conflict",
            api_key_id="key-ownerless-conflict",
            turn_state="http_turn_ownerless",
            session_header=None,
            previous_response_id="resp_live_owner",
        )

    assert exc_info.value.payload["error"]["code"] == "continuity_owner_conflict"


@pytest.mark.asyncio
async def test_durable_bridge_lookup_rejects_conflicting_turn_and_response_aliases(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    turn_owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-turn-owner",
        api_key_id="key-conflict",
        instance_id="instance-a",
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
async def test_durable_bridge_next_turn_prefers_verified_replay_over_shared_session_header(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("replay-1")
    shared_session = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-shared",
        api_key_id="key-replay",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-retired",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_session_header(
        session_id=shared_session.session_id,
        api_key_id="key-replay",
        session_header="sid-shared",
    )
    replay = await coordinator.claim_live_session(
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        api_key_id="key-replay",
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-replay",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=replay.session_id,
        api_key_id="key-replay",
        instance_id="instance-b",
        owner_epoch=replay.owner_epoch,
        turn_state="http_turn_replay",
        lease_ttl_seconds=120.0,
    )

    next_turn = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_replay",
        api_key_id="key-replay",
        turn_state="http_turn_replay",
        session_header="sid-shared",
        previous_response_id=None,
    )
    session_only = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-shared",
        api_key_id="key-replay",
        turn_state=None,
        session_header="sid-shared",
        previous_response_id=None,
    )

    assert next_turn is not None
    assert next_turn.session_id == replay.session_id
    assert is_http_bridge_account_neutral_replay(
        kind=next_turn.canonical_kind,
        key=next_turn.canonical_key,
    )
    assert session_only is not None
    assert session_only.session_id == shared_session.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("predecessor_kind", ["prompt_cache", "session_header", "turn_state_header"])
async def test_durable_verified_replay_alias_cannot_be_stolen_by_predecessor(
    coordinator: DurableBridgeSessionCoordinator,
    predecessor_kind: str,
) -> None:
    predecessor = await coordinator.claim_live_session(
        session_key_kind=predecessor_kind,
        session_key_value=f"old-{predecessor_kind}",
        api_key_id="key-alias-fence",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-old",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    assert (
        await coordinator.register_turn_state(
            session_id=predecessor.session_id,
            api_key_id="key-alias-fence",
            instance_id="instance-a",
            owner_epoch=predecessor.owner_epoch,
            turn_state="http_turn_fenced_replay",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key(f"fenced-{predecessor_kind}")
    replay = await coordinator.claim_live_session(
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        api_key_id="key-alias-fence",
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-replay",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert (
        await coordinator.register_turn_state(
            session_id=replay.session_id,
            api_key_id="key-alias-fence",
            instance_id="instance-b",
            owner_epoch=replay.owner_epoch,
            turn_state="http_turn_fenced_replay",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )
    assert (
        await coordinator.register_turn_state(
            session_id=predecessor.session_id,
            api_key_id="key-alias-fence",
            instance_id="instance-a",
            owner_epoch=predecessor.owner_epoch,
            turn_state="http_turn_fenced_replay",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.ALIAS_PROTECTED
    )

    resolved = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_fenced_replay",
        api_key_id="key-alias-fence",
        turn_state="http_turn_fenced_replay",
        session_header=None,
        previous_response_id=None,
    )
    assert resolved is not None
    assert resolved.session_id == replay.session_id


@pytest.mark.asyncio
async def test_concurrent_recovery_lanes_publish_only_one_active_turn_owner(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    coordinators = [DurableBridgeSessionCoordinator(async_session_factory) for _ in range(2)]
    claims = []
    for index, coordinator in enumerate(coordinators):
        replay_kind, replay_key = make_http_bridge_account_neutral_replay_key(f"concurrent-{index}")
        claims.append(
            await coordinator.claim_live_session(
                session_key_kind=replay_kind,
                session_key_value=replay_key,
                api_key_id="key-concurrent-recovery",
                instance_id=f"instance-{index}",
                owner_process_epoch="test-process",
                lease_ttl_seconds=120.0,
                account_id=f"acc-{index}",
                model="gpt-5.4",
                service_tier=None,
                latest_turn_state=None,
                latest_response_id=None,
                allow_takeover=True,
            )
        )

    async def register(index: int) -> DurableBridgeAliasRegistration:
        claim = claims[index]
        return await coordinators[index].register_turn_state(
            session_id=claim.session_id,
            api_key_id="key-concurrent-recovery",
            instance_id=f"instance-{index}",
            owner_epoch=claim.owner_epoch,
            turn_state="http_turn_concurrent_recovery",
            lease_ttl_seconds=120.0,
        )

    results = await asyncio.gather(register(0), register(1))

    assert results.count(DurableBridgeAliasRegistration.REGISTERED) == 1
    assert results.count(DurableBridgeAliasRegistration.ALIAS_PROTECTED) == 1
    winner_index = results.index(DurableBridgeAliasRegistration.REGISTERED)
    resolved = await coordinators[0].lookup_turn_state_target(
        turn_state="http_turn_concurrent_recovery",
        api_key_id="key-concurrent-recovery",
    )
    assert resolved is not None
    assert resolved.session_id == claims[winner_index].session_id


@pytest.mark.asyncio
async def test_recovery_lane_replaces_alias_with_nonnull_owner_and_null_lease(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    old_kind, old_key = make_http_bridge_account_neutral_replay_key("null-lease-old")
    old_recovery = await coordinator.claim_live_session(
        session_key_kind=old_kind,
        session_key_value=old_key,
        api_key_id="key-null-lease",
        instance_id="instance-old",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-old",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    assert (
        await coordinator.register_turn_state(
            session_id=old_recovery.session_id,
            api_key_id="key-null-lease",
            instance_id="instance-old",
            owner_epoch=old_recovery.owner_epoch,
            turn_state="http_turn_null_lease",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == old_recovery.session_id)
            .values(lease_expires_at=None)
        )
        await session.commit()

    new_kind, new_key = make_http_bridge_account_neutral_replay_key("null-lease-new")
    new_recovery = await coordinator.claim_live_session(
        session_key_kind=new_kind,
        session_key_value=new_key,
        api_key_id="key-null-lease",
        instance_id="instance-new",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-new",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    result = await coordinator.register_turn_state(
        session_id=new_recovery.session_id,
        api_key_id="key-null-lease",
        instance_id="instance-new",
        owner_epoch=new_recovery.owner_epoch,
        turn_state="http_turn_null_lease",
        lease_ttl_seconds=120.0,
    )

    assert result == DurableBridgeAliasRegistration.REGISTERED
    resolved = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_null_lease",
        api_key_id="key-null-lease",
    )
    assert resolved is not None
    assert resolved.session_id == new_recovery.session_id


@pytest.mark.asyncio
async def test_durable_bare_replay_prefix_does_not_receive_alias_protection(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    malformed = await coordinator.claim_live_session(
        session_key_kind=HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
        session_key_value=HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
        api_key_id=None,
        instance_id="instance-malformed",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-malformed",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    assert (
        await coordinator.register_turn_state(
            session_id=malformed.session_id,
            api_key_id=None,
            instance_id="instance-malformed",
            owner_epoch=malformed.owner_epoch,
            turn_state="http_turn_bare_replay_prefix",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )
    ordinary = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-valid-ordinary",
        api_key_id=None,
        instance_id="instance-ordinary",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-ordinary",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert (
        await coordinator.register_turn_state(
            session_id=ordinary.session_id,
            api_key_id=None,
            instance_id="instance-ordinary",
            owner_epoch=ordinary.owner_epoch,
            turn_state="http_turn_bare_replay_prefix",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )

    resolved = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_bare_replay_prefix",
        api_key_id=None,
    )
    assert resolved is not None
    assert resolved.session_id == ordinary.session_id


@pytest.mark.asyncio
async def test_durable_verified_replay_alias_does_not_replace_unrelated_internal_lane(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    internal = await coordinator.claim_live_session(
        session_key_kind="internal_request_parallel",
        session_key_value="unrelated-internal-lane",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-internal",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    assert (
        await coordinator.register_turn_state(
            session_id=internal.session_id,
            api_key_id=None,
            instance_id="instance-a",
            owner_epoch=internal.owner_epoch,
            turn_state="http_turn_internal_conflict",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.REGISTERED
    )
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("internal-conflict")
    replay = await coordinator.claim_live_session(
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-replay",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert (
        await coordinator.register_turn_state(
            session_id=replay.session_id,
            api_key_id=None,
            instance_id="instance-b",
            owner_epoch=replay.owner_epoch,
            turn_state="http_turn_internal_conflict",
            lease_ttl_seconds=120.0,
        )
        == DurableBridgeAliasRegistration.ALIAS_PROTECTED
    )

    resolved = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_internal_conflict",
        api_key_id=None,
        turn_state="http_turn_internal_conflict",
        session_header=None,
        previous_response_id=None,
    )
    assert resolved is not None
    assert resolved.session_id == internal.session_id


@pytest.mark.asyncio
async def test_durable_replay_alias_policy_is_scoped_to_conflicting_row(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    decoy = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-unrelated-rebindable",
        api_key_id="key-row-scope",
        instance_id="instance-decoy",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-decoy",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=decoy.session_id,
        api_key_id="key-row-scope",
        instance_id="instance-decoy",
        owner_epoch=decoy.owner_epoch,
        turn_state="http_turn_unrelated_rebindable",
        lease_ttl_seconds=120.0,
    )
    protected = await coordinator.claim_live_session(
        session_key_kind="internal_request_parallel",
        session_key_value="protected-internal-lane",
        api_key_id="key-row-scope",
        instance_id="instance-protected",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-protected",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=protected.session_id,
        api_key_id="key-row-scope",
        instance_id="instance-protected",
        owner_epoch=protected.owner_epoch,
        turn_state="http_turn_cross_row_protected",
        lease_ttl_seconds=120.0,
    )
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("cross-row-protected")
    replay = await coordinator.claim_live_session(
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        api_key_id="key-row-scope",
        instance_id="instance-replay",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-replay",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    result = await coordinator.register_turn_state(
        session_id=replay.session_id,
        api_key_id="key-row-scope",
        instance_id="instance-replay",
        owner_epoch=replay.owner_epoch,
        turn_state="http_turn_cross_row_protected",
        lease_ttl_seconds=120.0,
    )

    assert result == DurableBridgeAliasRegistration.ALIAS_PROTECTED
    resolved = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_cross_row_protected",
        api_key_id="key-row-scope",
    )
    assert resolved is not None
    assert resolved.session_id == protected.session_id


@pytest.mark.asyncio
async def test_durable_ordinary_rebind_ignores_unrelated_replay_alias(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("unrelated-replay")
    replay = await coordinator.claim_live_session(
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        api_key_id="key-row-scope-inverse",
        instance_id="instance-replay",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-replay",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=replay.session_id,
        api_key_id="key-row-scope-inverse",
        instance_id="instance-replay",
        owner_epoch=replay.owner_epoch,
        turn_state="http_turn_unrelated_replay",
        lease_ttl_seconds=120.0,
    )
    first = await coordinator.claim_live_session(
        session_key_kind="internal_unanchored_parallel",
        session_key_value="first-ordinary-owner",
        api_key_id="key-row-scope-inverse",
        instance_id="instance-first",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-first",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=first.session_id,
        api_key_id="key-row-scope-inverse",
        instance_id="instance-first",
        owner_epoch=first.owner_epoch,
        turn_state="http_turn_ordinary_rebind",
        lease_ttl_seconds=120.0,
    )
    second = await coordinator.claim_live_session(
        session_key_kind="internal_unanchored_parallel",
        session_key_value="second-ordinary-owner",
        api_key_id="key-row-scope-inverse",
        instance_id="instance-second",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-second",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    result = await coordinator.register_turn_state(
        session_id=second.session_id,
        api_key_id="key-row-scope-inverse",
        instance_id="instance-second",
        owner_epoch=second.owner_epoch,
        turn_state="http_turn_ordinary_rebind",
        lease_ttl_seconds=120.0,
    )

    assert result == DurableBridgeAliasRegistration.REGISTERED
    resolved = await coordinator.lookup_turn_state_target(
        turn_state="http_turn_ordinary_rebind",
        api_key_id="key-row-scope-inverse",
    )
    assert resolved is not None
    assert resolved.session_id == second.session_id


@pytest.mark.asyncio
async def test_durable_bridge_ordinary_unanchored_key_does_not_override_shared_session_header(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    shared_session = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-shared-ordinary",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-shared",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_session_header(
        session_id=shared_session.session_id,
        api_key_id=None,
        session_header="sid-shared-ordinary",
    )
    ordinary = await coordinator.claim_live_session(
        session_key_kind="internal_unanchored_parallel",
        session_key_value="a" * 64,
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-ordinary",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=ordinary.session_id,
        api_key_id=None,
        instance_id="instance-b",
        owner_epoch=ordinary.owner_epoch,
        turn_state="http_turn_ordinary",
        lease_ttl_seconds=120.0,
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await coordinator.lookup_request_targets(
            session_key_kind="turn_state_header",
            session_key_value="http_turn_ordinary",
            api_key_id=None,
            turn_state="http_turn_ordinary",
            session_header="sid-shared-ordinary",
            previous_response_id=None,
        )

    assert exc_info.value.payload["error"]["code"] == "continuity_owner_conflict"


@pytest.mark.asyncio
async def test_durable_bridge_verified_replay_does_not_hide_specific_alias_conflict(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("replay-conflict")
    replay = await coordinator.claim_live_session(
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-replay",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    response_owner = await coordinator.claim_live_session(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_response_owner",
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-response",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=replay.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=replay.owner_epoch,
        turn_state="http_turn_replay_conflict",
        lease_ttl_seconds=120.0,
    )
    await coordinator.register_previous_response_id(
        session_id=response_owner.session_id,
        api_key_id=None,
        instance_id="instance-b",
        owner_epoch=response_owner.owner_epoch,
        response_id="resp_other_owner",
        lease_ttl_seconds=120.0,
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await coordinator.lookup_request_targets(
            session_key_kind="request",
            session_key_value="request-conflict",
            api_key_id=None,
            turn_state="http_turn_replay_conflict",
            session_header=None,
            previous_response_id="resp_other_owner",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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

    assert stale_registered == DurableBridgeAliasRegistration.OWNER_FENCED
    assert current_registered == DurableBridgeAliasRegistration.REGISTERED
    assert await coordinator.lookup_turn_state_target(turn_state="http_turn_stale_owner", api_key_id=None) is None
    assert await coordinator.lookup_turn_state_target(turn_state="http_turn_current_owner", api_key_id=None) is not None


@pytest.mark.asyncio
async def test_durable_bridge_same_owner_reclaim_advances_epoch_to_fence_the_predecessor(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    """Claims come only from a successor in-memory session (a reused session
    renews instead of claiming), so a live same-owner row means the predecessor
    local session is retiring concurrently. The claim must advance the epoch so
    the predecessor's outstanding fenced release no-ops instead of racing the
    successor into a closed, ownerless row (issue #1695)."""
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=True,
    )

    assert renewed.session_id == claimed.session_id
    assert renewed.owner_epoch == claimed.owner_epoch + 1
    assert renewed.latest_turn_state == "http_turn_2"
    assert renewed.latest_response_id == "resp_2"

    # The predecessor's release carries the old epoch: it must be fenced out,
    # leaving the successor's claim live and owned.
    released = await coordinator.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=False,
    )
    assert released is not None
    assert released.owner_instance_id == "instance-a"
    assert released.state == HttpBridgeSessionState.ACTIVE
    assert released.owner_epoch == renewed.owner_epoch


@pytest.mark.asyncio
async def test_durable_bridge_account_change_advances_epoch_to_fence_stale_release(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-account-change",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
async def test_durable_bridge_clear_response_anchor_nulls_anchor_fields_but_keeps_turn_state(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-clear-anchor",
        api_key_id="key-1",
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state="http_turn_stuck",
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=claimed.session_id,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        turn_state="http_turn_stuck",
        lease_ttl_seconds=60.0,
    )
    await coordinator.register_previous_response_id(
        session_id=claimed.session_id,
        api_key_id="key-1",
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        response_id="resp_stuck_anchor",
        lease_ttl_seconds=60.0,
        input_item_count=5,
        input_full_fingerprint="c" * 64,
        pending_tool_calls={"call_stuck": "function_call"},
    )

    cleared = await coordinator.clear_live_session_response_anchor(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
    )

    assert cleared is not None
    assert cleared.latest_response_id is None
    assert cleared.latest_input_item_count is None
    assert cleared.latest_input_full_fingerprint is None
    assert cleared.latest_pending_tool_calls is None
    assert cleared.latest_turn_state == "http_turn_stuck"

    lookup_by_turn_state = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-clear-anchor",
        api_key_id="key-1",
        turn_state="http_turn_stuck",
        session_header=None,
        previous_response_id=None,
    )
    assert lookup_by_turn_state is not None
    assert lookup_by_turn_state.latest_response_id is None

    # The previous-response alias row itself is untouched: a client that
    # still supplies the stale id explicitly can resolve the session; only
    # the proxy's own no-anchor injection path stops using it.
    lookup_by_stale_alias = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-clear-anchor",
        api_key_id="key-1",
        turn_state=None,
        session_header=None,
        previous_response_id="resp_stuck_anchor",
    )
    assert lookup_by_stale_alias is not None
    assert lookup_by_stale_alias.session_id == claimed.session_id


@pytest.mark.asyncio
async def test_durable_bridge_clear_response_anchor_is_noop_after_epoch_advance(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-clear-anchor-stale-epoch",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    # A newer owner takes over (forced epoch advance) before the stuck
    # timeout handler on the old owner gets to clear the anchor.
    replaced = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-clear-anchor-stale-epoch",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=True,
        force_owner_epoch_advance=True,
    )
    assert replaced.owner_epoch == claimed.owner_epoch + 1

    stale_clear = await coordinator.clear_live_session_response_anchor(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
    )

    assert stale_clear is not None
    assert stale_clear.owner_epoch == replaced.owner_epoch
    assert stale_clear.latest_response_id == "resp_2"


@pytest.mark.asyncio
async def test_durable_bridge_claim_takes_over_after_release(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-123",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        pending_tool_calls={"call_shell": "custom_tool_call", "call_lookup": "function_call"},
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
    assert lookup.latest_pending_tool_calls == {
        "call_lookup": "function_call",
        "call_shell": "custom_tool_call",
    }


@pytest.mark.asyncio
async def test_durable_bridge_pending_tool_calls_are_bound_to_response_id(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-manifest-response",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.6-sol",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_previous_response_id(
        session_id=claimed.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        response_id="resp_manifest_old",
        lease_ttl_seconds=60.0,
        input_item_count=1,
        input_full_fingerprint="b" * 64,
        pending_tool_calls={"call_old": "function_call"},
    )

    # Simulate a rolling-upgrade writer that predates the manifest column.
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claimed.session_id)
            .values(latest_response_id="resp_manifest_new")
        )
        await session.commit()

    lookup = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-manifest-response",
        api_key_id=None,
        turn_state=None,
        session_header="sid-manifest-response",
        previous_response_id=None,
    )

    assert lookup is not None
    assert lookup.latest_response_id == "resp_manifest_new"
    assert lookup.latest_pending_tool_calls is None


@pytest.mark.asyncio
async def test_durable_bridge_takeover_with_account_change_clears_stale_aliases(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-alias-reset",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
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
        pending_tool_calls={"call_old": "function_call"},
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
        owner_process_epoch="test-process",
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
    assert reclaimed.latest_pending_tool_calls is None

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
        owner_process_epoch="test-process",
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


def test_durable_bridge_lookup_lease_accepts_offset_aware_timestamp() -> None:
    lookup = DurableBridgeLookup(
        session_id="session-aware-lease",
        canonical_kind="session_header",
        canonical_key="sid-aware-lease",
        api_key_scope="anonymous",
        account_id="acc-1",
        owner_instance_id="instance-a",
        owner_epoch=1,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state=None,
        latest_response_id=None,
    )

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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
        owner_process_epoch="test-process",
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
async def test_claim_refuses_live_draining_lease_without_allow_takeover(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-draining-claim",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
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

    refused = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-draining-claim",
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process-b",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=False,
    )

    assert refused.owner_instance_id == "instance-a"
    assert refused.state == "draining"
    assert refused.lease_expires_at == claimed.lease_expires_at
    assert refused.lease_is_active(now=utcnow()) is True


@pytest.mark.asyncio
async def test_claim_refuses_live_draining_lease_even_with_allow_takeover(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-draining-force",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
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

    refused = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-draining-force",
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process-b",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=True,
    )

    assert refused.owner_instance_id == "instance-a"
    assert refused.state == "draining"
    assert refused.lease_expires_at == claimed.lease_expires_at
    assert refused.lease_is_active(now=utcnow()) is True


@pytest.mark.asyncio
async def test_claim_takes_over_expired_draining_lease(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-draining-expired",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
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

    async with async_session_factory() as session:
        record = await session.get(HttpBridgeSessionRecord, claimed.session_id)
        assert record is not None
        record.lease_expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()

    stolen = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-draining-expired",
        api_key_id=None,
        instance_id="instance-b",
        owner_process_epoch="test-process-b",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_2",
        latest_response_id="resp_2",
        allow_takeover=False,
    )

    assert stolen.owner_instance_id == "instance-b"
    assert stolen.state == "active"
    assert stolen.lease_is_active(now=utcnow()) is True


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
        owner_process_epoch="test-process",
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
async def test_startup_reclassifies_submitted_operation_for_recovery(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    claimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-submitted-recovery",
        api_key_id=None,
        instance_id="instance-submitted-recovery",
        owner_process_epoch="old-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.6",
        service_tier=None,
        latest_turn_state="turn-state",
        latest_response_id=None,
        allow_takeover=True,
    )
    fingerprint = durable_bridge_hash("submitted-recovery")
    operation_id = durable_bridge_operation_id(claimed.session_id, fingerprint)
    async with async_session_factory() as session:
        repository = DurableBridgeRepository(session)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claimed.session_id,
            instance_id="instance-submitted-recovery",
            owner_epoch=claimed.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="acc-1",
            model="gpt-5.6",
            parent_response_id=None,
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claimed.session_id)
            .values(last_seen_at=utcnow() - timedelta(minutes=5))
        )
        await session.commit()

        deleted = await repository.purge_owned_sessions_on_startup(
            instance_id="instance-submitted-recovery",
            owner_process_epoch="new-process",
        )

        assert deleted == 0
        operation = await repository.get_operation(operation_id=operation_id)
        assert operation is not None
        assert operation.state == "unknown"


@pytest.mark.asyncio
async def test_startup_closes_same_instance_previous_process_epoch_rows(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    previous_process = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-reused-container",
        api_key_id=None,
        instance_id="container-74e8e7cda9fb",
        owner_process_epoch="boot-a",
        lease_ttl_seconds=120.0,
        account_id="acc-1",
        model="gpt-5.6-luna",
        service_tier=None,
        latest_turn_state="http_turn_reused_container",
        latest_response_id="resp_reused_container",
        allow_takeover=True,
    )
    await coordinator.register_session_header(
        session_id=previous_process.session_id,
        api_key_id=None,
        session_header="sid-reused-container",
    )
    current_process = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-current-process",
        api_key_id=None,
        instance_id="container-74e8e7cda9fb",
        owner_process_epoch="boot-b",
        lease_ttl_seconds=120.0,
        account_id="acc-1",
        model="gpt-5.6-luna",
        service_tier=None,
        latest_turn_state="http_turn_current_process",
        latest_response_id="resp_current_process",
        allow_takeover=True,
    )

    retired = await coordinator.purge_owned_sessions_on_startup(
        instance_id="container-74e8e7cda9fb",
        owner_process_epoch="boot-b",
        ownerless_cutoff=utcnow() - timedelta(seconds=60),
    )

    assert retired == 1
    current_lookup = await coordinator.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="sid-current-process",
        api_key_id=None,
        turn_state="http_turn_current_process",
        session_header="sid-current-process",
        previous_response_id="resp_current_process",
    )
    assert current_lookup is not None
    assert current_lookup.session_id == current_process.session_id
    assert current_lookup.owner_process_epoch == "boot-b"
    async with async_session_factory() as session:
        retired_row = await session.get(HttpBridgeSessionRecord, previous_process.session_id)
        retired_aliases = list(
            (
                await session.execute(
                    select(HttpBridgeSessionAlias).where(
                        HttpBridgeSessionAlias.session_id == previous_process.session_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert retired_row is not None
    assert retired_row.state == HttpBridgeSessionState.CLOSED
    assert retired_row.owner_instance_id is None
    assert retired_row.closed_at is not None
    assert retired_aliases == []


@pytest.mark.asyncio
async def test_startup_retirement_reclaim_does_not_resurrect_dead_anchors(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    previous_process = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-stable-reclaim",
        api_key_id=None,
        instance_id="container-stable",
        owner_process_epoch="boot-a",
        lease_ttl_seconds=120.0,
        account_id="acc-stable",
        model="gpt-5.6-luna",
        service_tier=None,
        latest_turn_state="http_turn_dead_anchor",
        latest_response_id="resp_dead_anchor",
        allow_takeover=True,
    )
    await coordinator.register_session_header(
        session_id=previous_process.session_id,
        api_key_id=None,
        session_header="sid-stable-reclaim",
    )
    await coordinator.register_previous_response_id(
        session_id=previous_process.session_id,
        api_key_id=None,
        instance_id="container-stable",
        owner_epoch=previous_process.owner_epoch,
        response_id="resp_dead_anchor",
        lease_ttl_seconds=120.0,
        input_item_count=7,
        input_full_fingerprint="d" * 64,
        pending_tool_calls={"call_dead": "function_call"},
    )

    retired = await coordinator.purge_owned_sessions_on_startup(
        instance_id="container-stable",
        owner_process_epoch="boot-b",
        ownerless_cutoff=utcnow() - timedelta(seconds=60),
    )
    reclaimed = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-stable-reclaim",
        api_key_id=None,
        instance_id="container-stable",
        owner_process_epoch="boot-b",
        lease_ttl_seconds=120.0,
        account_id="acc-stable",
        model="gpt-5.6-luna",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert retired == 1
    assert reclaimed.session_id == previous_process.session_id
    assert reclaimed.latest_turn_state is None
    assert reclaimed.latest_response_id is None
    assert reclaimed.latest_input_item_count is None
    assert reclaimed.latest_input_full_fingerprint is None
    assert reclaimed.latest_pending_tool_calls is None
    async with async_session_factory() as session:
        row = await session.get(HttpBridgeSessionRecord, previous_process.session_id)
    assert row is not None
    assert row.latest_turn_state is None
    assert row.latest_response_id is None
    assert row.latest_input_item_count is None
    assert row.latest_input_full_fingerprint is None
    assert row.latest_pending_tool_calls_json is None


@pytest.mark.asyncio
async def test_startup_retains_verified_replay_alias_as_ownerless_restart_proof(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    shared = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-shared-restart",
        api_key_id=None,
        instance_id="instance-shared",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-retired",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    await coordinator.register_session_header(
        session_id=shared.session_id,
        api_key_id=None,
        session_header="sid-shared-restart",
    )
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("restart-proof")
    replay = await coordinator.claim_live_session(
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        api_key_id=None,
        instance_id="instance-restarting",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-recovered",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id="resp-recovered",
        allow_takeover=True,
    )
    await coordinator.register_turn_state(
        session_id=replay.session_id,
        api_key_id=None,
        instance_id="instance-restarting",
        owner_epoch=replay.owner_epoch,
        turn_state="http_turn_recovered",
        lease_ttl_seconds=120.0,
    )
    retained_time = utcnow() - timedelta(seconds=30)
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == replay.session_id)
            .values(last_seen_at=retained_time, owner_process_epoch=None)
        )
        await session.commit()

    stale_kind, stale_key = make_http_bridge_account_neutral_replay_key("stale-restart-proof")
    stale_replay = await coordinator.claim_live_session(
        session_key_kind=stale_kind,
        session_key_value=stale_key,
        api_key_id=None,
        instance_id="instance-restarting",
        owner_process_epoch="test-process",
        lease_ttl_seconds=120.0,
        account_id="acc-stale-recovered",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    stale_time = utcnow() - timedelta(minutes=5)
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == stale_replay.session_id)
            .values(last_seen_at=stale_time, lease_expires_at=stale_time, owner_process_epoch=None)
        )
        await session.commit()

    deleted = await coordinator.purge_owned_sessions_on_startup(
        instance_id="instance-restarting",
        ownerless_cutoff=utcnow() - timedelta(seconds=60),
    )

    assert deleted == 1
    assert (
        await coordinator.lookup_request_targets(
            session_key_kind=stale_kind,
            session_key_value=stale_key,
            api_key_id=None,
            turn_state=None,
            session_header=None,
            previous_response_id=None,
        )
        is None
    )
    after_restart = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_recovered",
        api_key_id=None,
        turn_state="http_turn_recovered",
        session_header="sid-shared-restart",
        previous_response_id=None,
    )
    assert after_restart is not None
    assert after_restart.session_id == replay.session_id
    assert after_restart.owner_instance_id is None
    assert after_restart.state == HttpBridgeSessionState.DRAINING
    async with async_session_factory() as session:
        retained_record = await session.scalar(
            select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.id == replay.session_id)
        )
    assert retained_record is not None
    assert retained_record.last_seen_at == retained_time
    assert retained_record.lease_expires_at is not None
    assert retained_record.lease_expires_at <= utcnow()

    stale_time = utcnow() - timedelta(minutes=5)
    async with async_session_factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == replay.session_id)
            .values(last_seen_at=stale_time, lease_expires_at=stale_time)
        )
        await session.commit()

    stale_deleted = await coordinator.purge_owned_sessions_on_startup(
        instance_id="instance-other",
        ownerless_cutoff=utcnow() - timedelta(seconds=60),
    )

    assert stale_deleted == 1
    after_retention = await coordinator.lookup_request_targets(
        session_key_kind="turn_state_header",
        session_key_value="http_turn_recovered",
        api_key_id=None,
        turn_state="http_turn_recovered",
        session_header="sid-shared-restart",
        previous_response_id=None,
    )
    assert after_retention is not None
    assert after_retention.session_id == shared.session_id


@pytest.mark.asyncio
async def test_startup_retention_normalizes_aware_postgres_timestamps() -> None:
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("aware-startup-proof")
    candidate = SimpleNamespace(
        id="durable-aware-startup-proof",
        session_key_kind=replay_kind,
        session_key_value=replay_key,
        owner_instance_id="instance-a",
        last_seen_at=datetime.now(timezone.utc),
    )
    selected = SimpleNamespace(all=lambda: [candidate])
    exhausted = SimpleNamespace(all=lambda: [])
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[selected, SimpleNamespace(), exhausted]),
        scalars=AsyncMock(return_value=[]),
        commit=AsyncMock(),
    )
    repository = DurableBridgeRepository(cast(AsyncSession, session))

    deleted = await repository.purge_owned_sessions_on_startup(
        instance_id="instance-a",
        ownerless_cutoff=utcnow() - timedelta(seconds=60),
    )

    assert deleted == 0
    assert session.execute.await_count == 3
    session.commit.assert_awaited_once()


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
        owner_process_epoch="test-process",
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


@pytest.mark.asyncio
async def test_durable_bridge_retry_circuit_round_trip(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await coordinator.persist_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit",
        api_key_id="key-1",
        consecutive_failures=3,
        cooldown_until_epoch=1234.5,
        last_detail="stream_incomplete",
        updated_at_epoch=1200.0,
    )

    persisted = await coordinator.lookup_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit",
        api_key_id="key-1",
    )
    assert persisted is not None
    assert persisted.consecutive_failures == 3
    assert persisted.cooldown_until_epoch == 1234.5
    assert persisted.last_detail == "stream_incomplete"

    await coordinator.clear_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit",
        api_key_id="key-1",
    )
    cleared = await coordinator.lookup_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit",
        api_key_id="key-1",
    )
    assert cleared is not None
    assert cleared.consecutive_failures == 0
    assert cleared.cooldown_until_epoch == 0.0
    assert cleared.last_detail is None


@pytest.mark.asyncio
async def test_durable_bridge_retry_circuit_generation_claim_is_compare_and_set(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    await coordinator.persist_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-claim",
        api_key_id="key-claim",
        consecutive_failures=2,
        cooldown_until_epoch=1300.0,
        last_detail="stream_incomplete",
        updated_at_epoch=1200.0,
    )

    claimed = await coordinator.claim_retry_circuit_generation(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-claim",
        api_key_id="key-claim",
        expected_updated_at_epoch=1200.0,
        expected_admission_generation=0,
        expected_consecutive_failures=2,
        expected_cooldown_until_epoch=1300.0,
    )
    assert claimed is not None
    assert claimed.updated_at_epoch == 1200.0
    assert claimed.admission_generation == 1
    assert claimed.consecutive_failures == 2
    assert claimed.cooldown_until_epoch == 1300.0

    stale_claim = await coordinator.claim_retry_circuit_generation(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-claim",
        api_key_id="key-claim",
        expected_updated_at_epoch=1200.0,
        expected_admission_generation=0,
        expected_consecutive_failures=2,
        expected_cooldown_until_epoch=1300.0,
    )
    assert stale_claim is None

    absent_claim = await coordinator.claim_retry_circuit_generation(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-claim-absent",
        api_key_id="key-claim",
        expected_updated_at_epoch=None,
        expected_admission_generation=0,
        expected_consecutive_failures=0,
        expected_cooldown_until_epoch=0.0,
    )
    assert absent_claim is not None
    assert absent_claim.admission_generation == 1

    await coordinator.persist_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-claim",
        api_key_id="key-claim",
        consecutive_failures=3,
        cooldown_until_epoch=1400.0,
        last_detail="stream_incomplete",
        # Simulate a delayed replica whose wall clock is behind the observed
        # failure row. The admission claim must not disturb this base epoch.
        updated_at_epoch=1100.0,
        base_updated_at_epoch=1200.0,
    )
    after_delayed_failure = await coordinator.lookup_retry_circuit(
        session_key_kind="session_header",
        session_key_value="sid-retry-circuit-claim",
        api_key_id="key-claim",
    )
    assert after_delayed_failure is not None
    assert after_delayed_failure.consecutive_failures == 3
    assert after_delayed_failure.admission_generation == 1


def _lookup_with_lease(lease_expires_at):
    from app.db.models import HttpBridgeSessionState
    from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup

    return DurableBridgeLookup(
        session_id="sess-tz",
        canonical_kind="session_header",
        canonical_key="key-tz",
        api_key_scope="scope-tz",
        account_id="acc-tz",
        owner_instance_id="instance-a",
        owner_epoch=1,
        lease_expires_at=lease_expires_at,
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state=None,
        latest_response_id=None,
    )


def test_lease_is_active_accepts_timestamptz_aware_expiry():
    """lease_expires_at is a timestamptz column: PostgreSQL yields it
    offset-aware while utcnow() (and SQLite) are naive UTC. The raw
    comparison raised TypeError on the anchored-lookup hot path in
    production (v1.23.0-beta.5); lease_is_active must normalize."""
    from datetime import timedelta, timezone

    from app.core.utils.time import utcnow

    naive_now = utcnow()
    aware_future = (naive_now + timedelta(minutes=5)).replace(tzinfo=timezone.utc)
    aware_past = (naive_now - timedelta(minutes=5)).replace(tzinfo=timezone.utc)

    assert _lookup_with_lease(aware_future).lease_is_active(now=naive_now) is True
    assert _lookup_with_lease(aware_past).lease_is_active(now=naive_now) is False
    # Naive expiry against an aware clock must normalize the same way.
    aware_now = naive_now.replace(tzinfo=timezone.utc)
    assert _lookup_with_lease(naive_now + timedelta(minutes=5)).lease_is_active(now=aware_now) is True
    # Existing naive-vs-naive behaviour is unchanged.
    assert _lookup_with_lease(naive_now + timedelta(minutes=5)).lease_is_active(now=naive_now) is True
    assert _lookup_with_lease(None).lease_is_active(now=naive_now) is False


@pytest.mark.asyncio
async def test_durable_bridge_claim_survives_a_release_committing_mid_claim(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic reproduction of the #1695 CI flake.

    SQLite's with_for_update is a no-op, so the retiring predecessor's fenced
    release can commit between the successor claim's SELECT and its write.
    Before the fix, the claim mutated ORM attributes, SQLAlchemy omitted
    fields whose values matched the stale read (owner unchanged on a single
    instance), the release's owner=None/state=CLOSED survived the claim's
    commit, and the refresh handed the claimant a closed, ownerless row —
    surfaced to the client as 409 bridge_instance_mismatch.
    """
    predecessor = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-race",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="http_turn_1",
        latest_response_id="resp_1",
        allow_takeover=True,
    )

    import app.modules.proxy.durable_bridge_repository as repository_module

    real_writer_section = repository_module.sqlite_writer_section
    release_injected = False

    @contextlib.asynccontextmanager
    async def writer_section_with_interleaved_release():
        nonlocal release_injected
        if not release_injected:
            release_injected = True
            # The predecessor's fenced release lands exactly between the
            # successor claim's SELECT and its write.
            async with async_session_factory() as release_session:
                await DurableBridgeRepository(release_session).release_session(
                    session_id=predecessor.session_id,
                    instance_id="instance-a",
                    owner_epoch=predecessor.owner_epoch,
                    draining=False,
                )
        async with real_writer_section():
            yield

    monkeypatch.setattr(repository_module, "sqlite_writer_section", writer_section_with_interleaved_release)

    successor = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-race",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=False,
    )

    assert release_injected is True
    assert successor.session_id == predecessor.session_id
    # The claim's write must be authoritative over the interleaved release.
    assert successor.owner_instance_id == "instance-a"
    assert successor.state == HttpBridgeSessionState.ACTIVE
    assert successor.owner_epoch == predecessor.owner_epoch + 1


@pytest.mark.asyncio
async def test_durable_bridge_concurrent_successor_claims_serialize_on_the_epoch_cas(
    coordinator: DurableBridgeSessionCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two successor claims can both read epoch N (with_for_update is a no-op
    on SQLite). Without the compare-and-set, both would write N+1 and both
    believe they own the row with colliding fences. The loser must retry
    against fresh state and land on a distinct, higher epoch."""
    predecessor = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-cas",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    import app.modules.proxy.durable_bridge_repository as repository_module

    real_writer_section = repository_module.sqlite_writer_section
    competitor_epochs: list[int] = []
    injected = False

    @contextlib.asynccontextmanager
    async def writer_section_with_competing_claim():
        nonlocal injected
        if not injected:
            injected = True
            # A competing successor claim commits between this claim's SELECT
            # and its CAS write.
            competitor = await coordinator.claim_live_session(
                session_key_kind="session_header",
                session_key_value="sid-cas",
                api_key_id=None,
                instance_id="instance-a",
                owner_process_epoch="test-process",
                lease_ttl_seconds=60.0,
                account_id="acc-1",
                model="gpt-5.4",
                service_tier=None,
                latest_turn_state=None,
                latest_response_id=None,
                allow_takeover=False,
            )
            competitor_epochs.append(competitor.owner_epoch)
        async with real_writer_section():
            yield

    monkeypatch.setattr(repository_module, "sqlite_writer_section", writer_section_with_competing_claim)

    loser_turned_winner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-cas",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=False,
    )

    assert competitor_epochs == [predecessor.owner_epoch + 1]
    # The raced claim lost the CAS, retried against fresh state, and landed on
    # its own distinct epoch above the competitor's.
    assert loser_turned_winner.owner_instance_id == "instance-a"
    assert loser_turned_winner.owner_epoch == competitor_epochs[0] + 1


@pytest.mark.asyncio
async def test_durable_bridge_cas_loser_does_not_steal_a_foreign_winners_live_lease(
    coordinator: DurableBridgeSessionCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two replicas recovering the same released row both enter with takeover
    permission decided against that released state. Once one wins the CAS, the
    loser re-reads a live foreign ACTIVE lease — reusing the stale permission
    would steal it. The loser must fail closed and report the real owner, which
    the bridge surfaces as the cross-replica retry response."""
    released = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-foreign-cas",
        api_key_id=None,
        instance_id="instance-old",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    # The previous owner released: both recovering replicas legitimately see a
    # takeover-eligible row.
    await coordinator.release_live_session(
        session_id=released.session_id,
        instance_id="instance-old",
        owner_epoch=released.owner_epoch,
        draining=False,
    )

    import app.modules.proxy.durable_bridge_repository as repository_module

    real_writer_section = repository_module.sqlite_writer_section
    injected = False
    winner_epoch: list[int] = []

    @contextlib.asynccontextmanager
    async def writer_section_with_foreign_winner():
        nonlocal injected
        if not injected:
            injected = True
            winner = await coordinator.claim_live_session(
                session_key_kind="session_header",
                session_key_value="sid-foreign-cas",
                api_key_id=None,
                instance_id="instance-b",
                owner_process_epoch="test-process",
                lease_ttl_seconds=60.0,
                account_id="acc-1",
                model="gpt-5.4",
                service_tier=None,
                latest_turn_state=None,
                latest_response_id=None,
                allow_takeover=True,
            )
            winner_epoch.append(winner.owner_epoch)
        async with real_writer_section():
            yield

    monkeypatch.setattr(repository_module, "sqlite_writer_section", writer_section_with_foreign_winner)

    loser = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-foreign-cas",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="test-process",
        lease_ttl_seconds=60.0,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )

    assert winner_epoch, "the foreign winner must have claimed first"
    # The loser reports the winner as owner instead of stealing the live lease.
    assert loser.owner_instance_id == "instance-b"
    assert loser.owner_epoch == winner_epoch[0]
    assert loser.state == HttpBridgeSessionState.ACTIVE
