from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.utils.time import utcnow
from app.db.models import Account, Base, HttpBridgeOperationRecord, HttpBridgeSessionRecord
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import DurableBridgeOperationEventInput
from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher

pytestmark = pytest.mark.unit


@pytest.fixture
async def bridge(tmp_path) -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bridge.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    coordinator = DurableBridgeSessionCoordinator(factory)
    owner = await coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="dispatch-test",
        api_key_id=None,
        instance_id="worker",
        owner_process_epoch="process",
        lease_ttl_seconds=120,
        account_id=None,
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    authority: Any = dict(session_id=owner.session_id, instance_id="worker", owner_epoch=owner.owner_epoch)
    operation = await coordinator.record_operation(
        operation_id="operation",
        request_fingerprint="fingerprint",
        account_id=None,
        model="gpt-5.4",
        parent_response_id=None,
        request_text='{"input":"dispatch"}',
        **authority,
    )
    assert operation is not None and operation.dispatch_generation == 0
    yield SimpleNamespace(coordinator=coordinator, factory=factory, authority=authority, owner=owner)
    await engine.dispose()


async def claim(bridge, expected: int = 0):
    operation = await bridge.coordinator.claim_operation_dispatch(
        operation_id="operation",
        expected_dispatch_generation=expected,
        **bridge.authority,
    )
    assert operation is not None
    return operation


async def acknowledge(bridge, generation: int, response_id: str = "response") -> None:
    assert await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=generation,
        state="acknowledged",
        response_id=response_id,
        **bridge.authority,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("anchored", [False, True])
@pytest.mark.parametrize("acknowledged", [False, True])
async def test_account_rebind_requires_new_generation_and_unanchored_operation(bridge, anchored, acknowledged) -> None:
    await claim(bridge)
    if acknowledged:
        await acknowledge(bridge, 1)
    async with bridge.factory() as session:
        session.add(
            Account(
                id="replacement",
                email="replacement@example.test",
                plan_type="pro",
                access_token_encrypted=b"fixture",
                refresh_token_encrypted=b"fixture",
                id_token_encrypted=b"fixture",
                last_refresh=utcnow(),
            )
        )
        if anchored:
            await session.execute(
                update(HttpBridgeOperationRecord)
                .where(HttpBridgeOperationRecord.operation_id == "operation")
                .values(parent_response_id="parent-response")
            )
        await session.commit()
    assert (
        await bridge.coordinator.claim_operation_dispatch(
            operation_id="operation",
            expected_dispatch_generation=1,
            dispatch_account_id="replacement",
            expected_response_id="response" if acknowledged else None,
            **bridge.authority,
        )
        is None
    )
    rebound = await bridge.coordinator.claim_operation_dispatch(
        operation_id="operation",
        expected_dispatch_generation=1,
        dispatch_account_id="replacement",
        allow_account_rebind=True,
        expected_response_id="response" if acknowledged else None,
        **bridge.authority,
    )
    if anchored:
        assert rebound is None
        persisted = await bridge.coordinator.get_operation(operation_id="operation")
        assert persisted is not None and persisted.dispatch_generation == 1 and persisted.account_id is None
    else:
        assert rebound is not None and rebound.dispatch_generation == 2 and rebound.account_id == "replacement"
        assert not await bridge.coordinator.update_operation(
            operation_id="operation",
            expected_dispatch_generation=1,
            state="acknowledged",
            response_id="stale-response",
            **bridge.authority,
        )
        await acknowledge(bridge, 2, "replacement-response")


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_recorded", [False, True])
async def test_acknowledged_redispatch_requires_response_identity_and_pending_phase(bridge, terminal_recorded) -> None:
    await claim(bridge)
    await acknowledge(bridge, 1)
    if terminal_recorded:
        async with bridge.factory() as session:
            await session.execute(
                update(HttpBridgeOperationRecord)
                .where(HttpBridgeOperationRecord.operation_id == "operation")
                .values(terminal_append_phase="appended")
            )
            await session.commit()
    assert (
        await bridge.coordinator.claim_operation_dispatch(
            operation_id="operation",
            expected_dispatch_generation=1,
            expected_response_id="another-response",
            **bridge.authority,
        )
        is None
    )
    next_attempt = await bridge.coordinator.claim_operation_dispatch(
        operation_id="operation",
        expected_dispatch_generation=1,
        expected_response_id="response",
        **bridge.authority,
    )
    if terminal_recorded:
        assert next_attempt is None
    else:
        assert next_attempt is not None and next_attempt.dispatch_generation == 2 and next_attempt.response_id is None


@pytest.mark.asyncio
async def test_retention_does_not_recreate_dispatch_authority_under_an_old_operation_id(bridge) -> None:
    from app.modules.proxy._service.http_bridge.request_submit import _new_http_bridge_operation_id

    fingerprint = "retention-fingerprint"
    first_id = _new_http_bridge_operation_id(bridge.owner.session_id, fingerprint)
    next_id = _new_http_bridge_operation_id(bridge.owner.session_id, fingerprint)
    assert next_id != first_id
    assert len(first_id) <= 80 and len(next_id) <= 80
    registration = dict(
        request_fingerprint=fingerprint,
        account_id=None,
        model="gpt-5.4",
        parent_response_id=None,
        **bridge.authority,
    )
    first = await bridge.coordinator.record_operation(operation_id=first_id, **registration)
    assert first is not None and first.created
    assert (
        await bridge.coordinator.claim_operation_dispatch(
            operation_id=first_id,
            expected_dispatch_generation=0,
            **bridge.authority,
        )
        is not None
    )
    retained = await bridge.coordinator.record_operation(operation_id=next_id, **registration)
    assert retained is not None and retained.operation_id == first_id and not retained.created
    assert await bridge.coordinator.update_operation(
        operation_id=first_id,
        expected_dispatch_generation=1,
        state="failed",
        **bridge.authority,
    )
    purge = await bridge.coordinator.purge_operation_spool_batch(cutoff=utcnow() + timedelta(seconds=1))
    assert purge.deleted_operations == 1
    fresh = await bridge.coordinator.record_operation(operation_id=next_id, **registration)
    assert fresh is not None and fresh.created and fresh.dispatch_generation == 0
    dispatch = await bridge.coordinator.claim_operation_dispatch(
        operation_id=next_id,
        expected_dispatch_generation=0,
        **bridge.authority,
    )
    assert dispatch is not None and dispatch.dispatch_generation == 1
    assert not await bridge.coordinator.update_operation(
        operation_id=first_id,
        expected_dispatch_generation=1,
        state="acknowledged",
        response_id="late-purged-response",
        **bridge.authority,
    )
    current = await bridge.coordinator.get_operation(operation_id=next_id)
    assert current is not None and current.response_id is None and current.state == "submitted"


@pytest.mark.asyncio
async def test_preclaimed_recovery_handoff_fences_source_generation(bridge) -> None:
    await claim(bridge)
    pending = await bridge.coordinator.reset_operation_event_spool(
        operation_id="operation",
        expected_dispatch_generation=1,
        **bridge.authority,
    )
    assert pending is not None and pending.dispatch_generation == 2
    async with bridge.factory() as session:
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == bridge.owner.session_id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()
    successor = await bridge.coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="replacement",
        api_key_id=None,
        instance_id="worker",
        owner_process_epoch="process",
        lease_ttl_seconds=120,
        account_id=None,
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=None,
        latest_response_id=None,
        allow_takeover=True,
    )
    registration = dict(
        operation_id="operation",
        request_fingerprint="fingerprint",
        account_id=None,
        model="gpt-5.4",
        parent_response_id=None,
        session_id=successor.session_id,
        instance_id="worker",
        owner_epoch=successor.owner_epoch,
    )
    stale = await bridge.coordinator.record_operation(expected_rebind_dispatch_generation=1, **registration)
    assert stale is not None and not stale.rebound and stale.session_id == bridge.owner.session_id
    rebound = await bridge.coordinator.record_operation(expected_rebind_dispatch_generation=2, **registration)
    assert rebound is not None and rebound.rebound and rebound.dispatch_generation == 3
    assert rebound.session_id == successor.session_id
    assert not await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=2,
        state="acknowledged",
        response_id="source-response",
        **bridge.authority,
    )
    assert await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=3,
        state="acknowledged",
        response_id="replacement-response",
        session_id=successor.session_id,
        instance_id="worker",
        owner_epoch=successor.owner_epoch,
    )


@pytest.mark.asyncio
async def test_coordinator_reader_unknown_transition_preserves_generation_fence(bridge) -> None:
    await claim(bridge)
    await claim(bridge, 1)
    assert not await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=1,
        state="unknown",
        **bridge.authority,
    )
    assert await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=2,
        state="unknown",
        **bridge.authority,
    )
    current = await bridge.coordinator.get_operation(operation_id="operation")
    assert current is not None and current.state == "unknown" and current.dispatch_generation == 2
    assert await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=2,
        state="failed",
        **bridge.authority,
    )
    assert not await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=2,
        state="unknown",
        **bridge.authority,
    )


@pytest.mark.asyncio
async def test_failed_reader_discard_preserves_successor_generation_buffer(bridge) -> None:
    await claim(bridge)
    await claim(bridge, 1)
    await acknowledge(bridge, 2, "replacement-response")
    batcher = HttpBridgeOperationEventBatcher(bridge.coordinator, max_bytes=8192, flush_interval_seconds=60)
    try:
        await batcher.enqueue(
            operation_id="operation",
            expected_dispatch_generation=1,
            expected_response_id=None,
            event_text="obsolete",
            **bridge.authority,
        )
        await batcher.enqueue(
            operation_id="operation",
            expected_dispatch_generation=2,
            expected_response_id="replacement-response",
            event_text="replacement",
            **bridge.authority,
        )
        await batcher.discard_operation(operation_id="operation", expected_dispatch_generation=1)
        await batcher.flush_operation(operation_id="operation", expected_dispatch_generation=2)
        assert await bridge.coordinator.get_operation_events(operation_id="operation") == ["replacement"]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_initial_claim_is_monotonic_and_exclusive(bridge) -> None:
    first = await claim(bridge)
    assert first.dispatch_generation == 1
    assert first.recovery_dispatch_count == 0
    assert (
        await bridge.coordinator.claim_operation_dispatch(
            operation_id="operation",
            expected_dispatch_generation=0,
            **bridge.authority,
        )
        is None
    )
    second = await claim(bridge, 1)
    assert second.dispatch_generation == 2
    assert second.recovery_dispatch_count == 0


@pytest.mark.asyncio
async def test_admitted_initial_dispatch_survives_owner_replacement(bridge) -> None:
    successor = await bridge.coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="dispatch-test",
        api_key_id=None,
        instance_id="worker",
        owner_process_epoch="process",
        lease_ttl_seconds=120,
        account_id=None,
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="successor",
        latest_response_id="successor-response",
        allow_takeover=True,
    )
    assert successor.owner_epoch > bridge.owner.owner_epoch
    assert (
        await bridge.coordinator.claim_operation_dispatch(
            operation_id="operation",
            expected_dispatch_generation=0,
            **bridge.authority,
        )
        is None
    )
    initial = await bridge.coordinator.claim_operation_dispatch(
        operation_id="operation",
        expected_dispatch_generation=0,
        allow_admitted_initial=True,
        **bridge.authority,
    )
    assert initial is not None and initial.dispatch_generation == 1
    assert (
        await bridge.coordinator.claim_operation_dispatch(
            operation_id="operation",
            expected_dispatch_generation=1,
            allow_admitted_initial=True,
            **bridge.authority,
        )
        is None
    )
    registered = await bridge.coordinator.record_operation(
        operation_id="late-admitted-root",
        request_fingerprint="late-admitted-root",
        account_id=None,
        model="gpt-5.4",
        parent_response_id=None,
        allow_admitted_initial=True,
        **bridge.authority,
    )
    assert registered is not None and registered.dispatch_generation == 0
    assert (
        await bridge.coordinator.record_operation(
            operation_id="late-admitted-root",
            request_fingerprint="late-admitted-root",
            account_id=None,
            model="gpt-5.4",
            parent_response_id=None,
            allow_admitted_initial=True,
            **bridge.authority,
        )
        is None
    )


@pytest.mark.asyncio
async def test_failed_rebind_claims_generation_before_clearing_spool(bridge) -> None:
    await claim(bridge)
    assert await bridge.coordinator.append_terminal_operation_event(
        operation_id="operation",
        expected_dispatch_generation=1,
        state="failed",
        event_text="failure",
        max_bytes=1024,
        **bridge.authority,
    )
    rebound = await bridge.coordinator.record_operation(
        operation_id="operation",
        request_fingerprint="fingerprint",
        account_id=None,
        model="gpt-5.4",
        parent_response_id=None,
        **bridge.authority,
    )
    assert rebound is not None and rebound.rebound
    assert rebound.dispatch_generation == 2 and rebound.recovery_dispatch_count == 0
    assert await bridge.coordinator.get_operation_events(operation_id="operation") == []
    assert not await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=1,
        state="acknowledged",
        response_id="stale",
        **bridge.authority,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "writer",
    [
        "bind",
        "event",
        "batch",
        "chunk",
        "terminal",
        "terminal_chunk",
        "finalize",
        "settle",
        "unknown",
        "rollback",
        "reset",
    ],
)
async def test_stale_generation_never_changes_successor(bridge, writer: str) -> None:
    await claim(bridge)
    await claim(bridge, 1)
    await acknowledge(bridge, 2)
    current = await bridge.coordinator.get_operation(operation_id="operation")
    args = dict(operation_id="operation", expected_dispatch_generation=1, **bridge.authority)
    event = DurableBridgeOperationEventInput(
        event_text="stale",
        expected_dispatch_generation=1,
        expected_response_id="response",
        operation_id="operation",
        **bridge.authority,
    )
    coordinator = bridge.coordinator
    if writer == "bind":
        result = await coordinator.update_operation(**args, state="acknowledged", response_id="response")
    elif writer == "event":
        result = await coordinator.append_operation_event(
            **args, expected_response_id="response", event_text="stale", max_bytes=1024
        )
    elif writer in {"batch", "chunk"}:
        method = coordinator.append_operation_events if writer == "batch" else coordinator.append_operation_event_chunk
        result = await method(events=[event], max_bytes=1024)
    elif writer in {"terminal", "terminal_chunk"}:
        method = (
            coordinator.append_terminal_operation_event
            if writer == "terminal"
            else coordinator.append_terminal_operation_chunk
        )
        result = await method(**args, state="completed", response_id="response", event_text="stale", max_bytes=0)
    elif writer == "finalize":
        result = await coordinator.finalize_operation_event_spool(
            **args, expected_state="completed", expected_response_id="response"
        )
    elif writer == "settle":
        result = await coordinator.settle_terminal_append_failure(
            **args, state="completed", expected_response_id="response", response_id="response"
        )
    elif writer == "unknown":
        result = await coordinator.mark_operation_unknown(**args)
    elif writer == "rollback":
        result = await coordinator.rollback_operation_before_dispatch(**args)
    else:
        result = await coordinator.reset_operation_event_spool(**args)
    assert not result
    assert await coordinator.get_operation(operation_id="operation") == current
    assert await coordinator.get_operation_events(operation_id="operation") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["rows_v1", "chunks_v2"])
async def test_detached_predecessor_finishes_without_session_publication(bridge, format: str) -> None:
    await claim(bridge)
    successor = await bridge.coordinator.claim_live_session(
        session_key_kind="session_header",
        session_key_value="dispatch-test",
        api_key_id=None,
        instance_id="worker",
        owner_process_epoch="process",
        lease_ttl_seconds=120,
        account_id=None,
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state="successor-turn",
        latest_response_id="successor-response",
        allow_takeover=True,
    )
    assert successor.owner_epoch > bridge.owner.owner_epoch
    await acknowledge(bridge, 1, "predecessor-response")
    batcher = HttpBridgeOperationEventBatcher(bridge.coordinator, max_bytes=1024, spool_format=format)
    try:
        await batcher.enqueue(
            operation_id="operation",
            expected_dispatch_generation=1,
            expected_response_id="predecessor-response",
            event_text="created",
            **bridge.authority,
        )
        assert await batcher.append_terminal_event(
            operation_id="operation",
            expected_dispatch_generation=1,
            event_text="completed",
            max_bytes=1024,
            state="completed",
            response_id="predecessor-response",
            **bridge.authority,
        )
        # Deterministically confirm deferred finalization, without sleep.
        assert (
            await bridge.coordinator.finalize_operation_event_spool(
                operation_id="operation",
                expected_dispatch_generation=1,
                expected_state="completed",
                expected_response_id="predecessor-response",
                **bridge.authority,
            )
            or (await bridge.coordinator.get_operation(operation_id="operation")).event_spool_complete
        )
        operation = await bridge.coordinator.get_operation(operation_id="operation")
        assert operation is not None and operation.state == "completed" and operation.event_spool_complete
        assert await bridge.coordinator.get_operation_events(operation_id="operation") == ["created", "completed"]
        renewal = await bridge.coordinator.renew_live_session(
            api_key_id=None,
            lease_ttl_seconds=120,
            latest_turn_state="stale-turn",
            latest_response_id="stale-response",
            **bridge.authority,
        )
        assert renewal is None or renewal.owner_epoch == successor.owner_epoch
        lookup = (await bridge.coordinator.lookup_sessions(session_ids=[successor.session_id]))[0]
        assert lookup is not None and lookup.owner_epoch == successor.owner_epoch
        assert lookup.latest_response_id == "successor-response" and lookup.latest_turn_state == "successor-turn"
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_recovery_budget_refund_is_independent_and_idempotent(bridge) -> None:
    await claim(bridge)
    assert await bridge.coordinator.mark_operation_unknown(
        operation_id="operation",
        expected_dispatch_generation=1,
        **bridge.authority,
    )
    recovery = await bridge.coordinator.claim_unknown_operation_for_recovery(
        operation_id="operation",
        expected_dispatch_generation=1,
        max_recovery_dispatches=1,
        **bridge.authority,
    )
    assert recovery is not None and recovery.dispatch_generation == 2 and recovery.recovery_dispatch_count == 1
    assert await bridge.coordinator.mark_operation_unknown(
        operation_id="operation",
        expected_dispatch_generation=2,
        restore_recovery_dispatch_claim=True,
        expected_recovery_dispatch_count=1,
        **bridge.authority,
    )
    assert not await bridge.coordinator.mark_operation_unknown(
        operation_id="operation",
        expected_dispatch_generation=2,
        restore_recovery_dispatch_claim=True,
        expected_recovery_dispatch_count=1,
        **bridge.authority,
    )
    operation = await bridge.coordinator.get_operation(operation_id="operation")
    assert operation is not None and operation.dispatch_generation == 2 and operation.recovery_dispatch_count == 0
    next_attempt = await bridge.coordinator.claim_unknown_operation_for_recovery(
        operation_id="operation",
        expected_dispatch_generation=2,
        max_recovery_dispatches=1,
        **bridge.authority,
    )
    assert (
        next_attempt is not None and next_attempt.dispatch_generation == 3 and next_attempt.recovery_dispatch_count == 1
    )


@pytest.mark.asyncio
async def test_claimed_cleanup_cannot_reuse_generation(bridge) -> None:
    await claim(bridge)
    assert await bridge.coordinator.rollback_operation_before_dispatch(
        operation_id="operation",
        expected_dispatch_generation=1,
        **bridge.authority,
    )
    operation = await bridge.coordinator.get_operation(operation_id="operation")
    assert operation is not None and operation.dispatch_generation == 1 and operation.state == "failed"
    assert (await claim(bridge, 1)).dispatch_generation == 2


@pytest.mark.asyncio
async def test_legacy_ambiguous_row_fails_closed_but_terminal_read_is_unchanged(bridge) -> None:
    async with bridge.factory() as session:
        await session.execute(update(HttpBridgeOperationRecord).values(dispatch_generation=None, state="unknown"))
        await session.commit()
    assert (
        await bridge.coordinator.claim_operation_dispatch(
            operation_id="operation",
            expected_dispatch_generation=0,
            **bridge.authority,
        )
        is None
    )
    assert (
        await bridge.coordinator.claim_unknown_operation_for_recovery(
            operation_id="operation",
            expected_dispatch_generation=None,
            **bridge.authority,
        )
        is None
    )
    assert not await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=None,
        state="completed",
        **bridge.authority,
    )
    async with bridge.factory() as session:
        row = await session.scalar(select(HttpBridgeOperationRecord))
        assert row is not None and row.dispatch_generation is None and row.state == "unknown"
        row.state = "completed"
        row.event_spool_complete = True
        await session.commit()
    historical = await bridge.coordinator.get_operation(operation_id="operation")
    assert historical is not None and historical.state == "completed" and historical.event_spool_complete


@pytest.mark.asyncio
async def test_old_batch_failure_does_not_poison_new_generation() -> None:
    captured = []

    async def append(*, events, max_bytes):
        captured.append(events)
        return events[0].expected_dispatch_generation == 2

    durable = SimpleNamespace(append_operation_events=append)
    batcher = HttpBridgeOperationEventBatcher(durable, max_bytes=1024, flush_interval_seconds=3600)
    try:
        for generation in (1, 2):
            await batcher.enqueue(
                operation_id="same",
                session_id="session",
                instance_id="worker",
                owner_epoch=1,
                expected_dispatch_generation=generation,
                event_text=str(generation),
            )
        assert not await batcher.flush_pending_operation(operation_id="same", expected_dispatch_generation=1)
        assert await batcher.flush_pending_operation(operation_id="same", expected_dispatch_generation=2)
        assert [events[0].expected_dispatch_generation for events in captured] == [1, 2]
    finally:
        await batcher.close()


@pytest.mark.asyncio
async def test_bound_identity_and_terminal_phase_cannot_be_overwritten(bridge) -> None:
    await claim(bridge)
    await acknowledge(bridge, 1)
    assert not await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=1,
        state="acknowledged",
        response_id="other",
        **bridge.authority,
    )
    assert await bridge.coordinator.append_terminal_operation_event(
        operation_id="operation",
        expected_dispatch_generation=1,
        state="completed",
        response_id="response",
        event_text="completed",
        max_bytes=1024,
        **bridge.authority,
    )
    assert not await bridge.coordinator.update_operation(
        operation_id="operation",
        expected_dispatch_generation=1,
        state="failed",
        response_id="response",
        **bridge.authority,
    )
    assert not await bridge.coordinator.append_operation_event(
        operation_id="operation",
        expected_dispatch_generation=1,
        expected_response_id="response",
        event_text="late",
        max_bytes=1024,
        **bridge.authority,
    )
