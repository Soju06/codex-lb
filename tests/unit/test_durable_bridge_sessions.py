from __future__ import annotations

import asyncio
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
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeAliasRegistration,
    DurableBridgeRepository,
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
async def test_reversible_recovery_turn_state_registration_restores_previous_owner(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    predecessor = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-recovery-predecessor",
        api_key_id=None,
        instance_id="instance-a",
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
async def test_durable_bridge_next_turn_prefers_verified_replay_over_shared_session_header(
    coordinator: DurableBridgeSessionCoordinator,
) -> None:
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("replay-1")
    shared_session = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-shared",
        api_key_id="key-replay",
        instance_id="instance-a",
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

    assert stale_registered == DurableBridgeAliasRegistration.OWNER_FENCED
    assert current_registered == DurableBridgeAliasRegistration.REGISTERED
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
async def test_startup_retains_verified_replay_alias_as_ownerless_restart_proof(
    coordinator: DurableBridgeSessionCoordinator,
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    shared = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="sid-shared-restart",
        api_key_id=None,
        instance_id="instance-shared",
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
            .values(last_seen_at=retained_time)
        )
        await session.commit()

    stale_kind, stale_key = make_http_bridge_account_neutral_replay_key("stale-restart-proof")
    stale_replay = await coordinator.claim_live_session(
        session_key_kind=stale_kind,
        session_key_value=stale_key,
        api_key_id=None,
        instance_id="instance-restarting",
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
            .values(last_seen_at=stale_time, lease_expires_at=stale_time)
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
