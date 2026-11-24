from __future__ import annotations

import json

import pytest

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import quarantine
from tests.integration.http_quarantine_fixtures import arm, complete, seed_durable_poison
from tests.integration.http_quarantine_fixtures import quarantine_clock as quarantine_clock
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as cleanup_http_bridge_sessions,  # noqa: F401
)
from tests.integration.test_http_responses_bridge import _promotion_history
from tests.integration.test_http_responses_bridge import promotion_transport as promotion_transport

pytestmark = pytest.mark.integration


def _assert_survives(service, session, evidence, deadline):
    entry = quarantine._http_bridge_quarantine_registry(service).get(session.key)
    assert entry is not None
    if evidence == "first-strike":
        assert entry.consecutive_eventless_timeouts == 1
        assert entry.quarantined_until == 0.0
        assert quarantine._http_bridge_session_key_poison_quarantined(service, session.key) is False
    else:
        assert entry.reason == (
            "retry_circuit_poisoned_anchor" if evidence == "poison" else "reattach_missing_response_created"
        )
        assert entry.quarantined_until == deadline
        assert quarantine._http_bridge_session_key_quarantined(service, session.key) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["poison", "weaker", "first-strike"])
async def test_failure_before_completion_pending_lock_survives(
    async_client, app_instance, promotion_transport, monkeypatch, evidence
):
    service = get_proxy_service_for_app(app_instance)
    process = service._process_http_bridge_upstream_text
    original = None
    deadline = None
    injected = False

    class InjectingLock:
        def __init__(self, session):
            self.session = session
            self.lock = session.pending_lock

        async def __aenter__(self):
            nonlocal injected, deadline
            await self.lock.acquire()
            if not injected:
                injected = True
                deadline = arm(service, self.session, evidence)

        async def __aexit__(self, *args):
            self.lock.release()

    async def track(session, text, *args, **kwargs):
        nonlocal original
        if json.loads(text).get("type") != "response.completed":
            return await process(session, text, *args, **kwargs)
        original = session
        lock = session.pending_lock
        session.pending_lock = InjectingLock(session)
        try:
            return await process(session, text, *args, **kwargs)
        finally:
            session.pending_lock = lock

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", track)
    await complete(async_client)
    assert injected and original is not None
    _assert_survives(service, original, evidence, deadline)


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["poison", "weaker", "first-strike"])
@pytest.mark.parametrize("failure_origin", ["completion-load", "replay-origin"])
async def test_durable_disproof_preserves_local_failure_after_captured_authority(
    async_client, app_instance, promotion_transport, monkeypatch, evidence, failure_origin
):
    service = get_proxy_service_for_app(app_instance)
    process = service._process_http_bridge_upstream_text
    lookup = service._durable_bridge.lookup_retry_circuit
    completing = None
    original = None
    injected = False
    deadline = None

    async def track(session, text, *args, **kwargs):
        nonlocal completing, original
        if json.loads(text).get("type") != "response.completed":
            return await process(session, text, *args, **kwargs)
        original = session
        await seed_durable_poison(
            service,
            session_key_kind=session.key.affinity_kind,
            session_key_value=session.key.affinity_key,
            api_key_id=session.key.api_key_id,
        )
        assert await service._load_http_bridge_retry_circuit(session)
        assert quarantine._http_bridge_session_key_poison_quarantined(service, session.key)
        if failure_origin == "replay-origin":
            owner = next(iter(session.pending_requests))
            # The real recovery test separately proves dispatch transports this metadata.
            owner.verified_stale_anchor_replay = True
            owner.verified_stale_anchor_retry_circuit_key = session.key
            owner.verified_stale_anchor_quarantine_generation = quarantine._http_bridge_quarantine_clear_fence(
                service, session.key
            )
            owner.verified_stale_anchor_quarantine_local_failure_fence = quarantine._http_bridge_local_failure_fence(
                service
            )
            arm(service, session, evidence)
        completing = session
        try:
            return await process(session, text, *args, **kwargs)
        finally:
            completing = None

    async def load(**kwargs):
        nonlocal injected, deadline
        if completing is not None and not injected:
            injected = True
            if failure_origin == "completion-load":
                arm(service, completing, evidence)
            deadline = 0.0 if evidence == "first-strike" else 700.0
            await service._durable_bridge.clear_retry_circuit(**kwargs)
        return await lookup(**kwargs)

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", track)
    monkeypatch.setattr(service._durable_bridge, "lookup_retry_circuit", load)
    await complete(async_client, session_id="quarantine-revoke")
    assert injected and original is not None
    if evidence == "weaker":
        # The shared deadline still included the old durable poison at injection.
        entry = quarantine._http_bridge_quarantine_registry(service)[original.key]
        assert entry.reason == "reattach_missing_response_created"
        assert entry.quarantined_until == 700.0
    else:
        _assert_survives(service, original, evidence, deadline)


@pytest.mark.asyncio
async def test_revoked_local_deadline_is_not_reused_by_next_completion_failure(
    async_client, app_instance, promotion_transport, monkeypatch
):
    service = get_proxy_service_for_app(app_instance)
    process = service._process_http_bridge_upstream_text
    load = service._load_http_bridge_retry_circuit
    completing = None
    original = None
    injected = False

    async def track(session, text, *args, **kwargs):
        nonlocal completing, original
        if json.loads(text).get("type") != "response.completed":
            return await process(session, text, *args, **kwargs)
        arm(service, session, "weaker")
        quarantine._quarantine_http_bridge_session(
            service, session, reason="retry_circuit_poisoned_anchor", minimum_seconds=2000.0
        )
        assert quarantine._revoke_http_bridge_poison_quarantine(
            service, session.key, generation=quarantine._http_bridge_quarantine_clear_fence(service, session.key)
        )
        assert quarantine._http_bridge_quarantine_registry(service)[session.key].quarantined_until == 700.0
        completing = session
        original = session
        try:
            return await process(session, text, *args, **kwargs)
        finally:
            completing = None

    async def inject(session, *args, **kwargs):
        nonlocal injected
        if completing is not None and not injected:
            injected = True
            arm(service, session, "poison")
        return await load(session, *args, **kwargs)

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", track)
    monkeypatch.setattr(service, "_load_http_bridge_retry_circuit", inject)
    await complete(async_client)
    assert injected and original is not None
    entry = quarantine._http_bridge_quarantine_registry(service)[original.key]
    assert entry.poison_quarantined_until == 700.0
    assert entry.quarantined_until == 700.0


@pytest.mark.asyncio
async def test_first_touch_durable_poison_uses_planning_probe_without_owner_failure(
    async_client, app_instance, promotion_transport, monkeypatch
):
    service = get_proxy_service_for_app(app_instance)
    lookup = service._durable_bridge.lookup_retry_circuit
    seeded = False

    async def load(**kwargs):
        nonlocal seeded
        if not seeded:
            seeded = True
            assert not service._http_bridge_sessions
            await seed_durable_poison(service, cooldown=-1.0, **kwargs)
        return await lookup(**kwargs)

    monkeypatch.setattr(service._durable_bridge, "lookup_retry_circuit", load)
    await complete(async_client, session_id="planning-poison")
    assert seeded
    assert not quarantine._http_bridge_quarantine_registry(service)


@pytest.mark.asyncio
async def test_completion_keeps_preexisting_suppressed_weaker_deadline(
    async_client, app_instance, promotion_transport, monkeypatch
):
    service = get_proxy_service_for_app(app_instance)
    process = service._process_http_bridge_upstream_text
    original = None

    async def track(session, text, *args, **kwargs):
        nonlocal original
        if json.loads(text).get("type") == "response.completed":
            original = session
            arm(service, session, "weaker")
            quarantine._quarantine_http_bridge_session(
                service, session, reason="retry_circuit_poisoned_anchor", minimum_seconds=900.0
            )
        return await process(session, text, *args, **kwargs)

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", track)
    await complete(async_client)
    assert original is not None
    entry = quarantine._http_bridge_quarantine_registry(service)[original.key]
    assert entry.reason == "reattach_missing_response_created"
    assert entry.quarantined_until == 700.0
    assert quarantine._http_bridge_session_key_poison_quarantined(service, original.key) is False


@pytest.mark.asyncio
async def test_all_poison_soft_cap_does_not_classify_unrelated_request_as_poisoned(
    async_client, app_instance, promotion_transport, monkeypatch
):
    from dataclasses import replace

    service = get_proxy_service_for_app(app_instance)
    await complete(async_client)
    original = next(iter(service._http_bridge_sessions.values()))
    monkeypatch.setattr(quarantine, "_HTTP_BRIDGE_QUARANTINE_MAX_ENTRIES", 3)
    for index in range(4):
        poisoned = replace(original, key=replace(original.key, affinity_key=f"poisoned-{index}"))
        arm(service, poisoned, "poison")
    registry = quarantine._http_bridge_quarantine_registry(service)
    assert len(registry) == 4
    assert quarantine._http_bridge_session_key_poison_quarantined(service, original.key) is False
    await complete(async_client, history=_promotion_history("unrelated"))
    assert len(registry) == 4
    assert len(promotion_transport[0]) == 2
    assert not promotion_transport[1]
