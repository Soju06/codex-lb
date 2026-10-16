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
from tests.integration.test_http_responses_bridge import promotion_transport as promotion_transport

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["none", "weaker", "poison", "first-strike"])
@pytest.mark.parametrize("retry_load", [False, True], ids=["preload", "failed-load-retry"])
@pytest.mark.parametrize("outcome", ["success", "settle-failure", "alias-failure"])
async def test_completion_separates_local_failure_from_durable_adoption(
    async_client, app_instance, promotion_transport, monkeypatch, evidence, retry_load, outcome
):
    service = get_proxy_service_for_app(app_instance)
    lookup = service._durable_bridge.lookup_retry_circuit
    clear = service._durable_bridge.clear_retry_circuit
    register = service._register_http_bridge_previous_response_id
    process = service._process_http_bridge_upstream_text
    completing = None
    original = None
    lookups = 0
    seeded = False
    local_deadline = None
    registered = False
    settled = False

    async def track(session, text, *args, **kwargs):
        nonlocal completing, original
        completing = session if json.loads(text).get("type") == "response.completed" else None
        if completing is not None:
            original = session
        try:
            return await process(session, text, *args, **kwargs)
        finally:
            completing = None

    async def load(**kwargs):
        nonlocal lookups, seeded, local_deadline
        if completing is not None:
            lookups += 1
            if not seeded:
                seeded = True
                assert completing.key.strength == "hard"
                await seed_durable_poison(service, **kwargs)
                if evidence != "none":
                    local_deadline = arm(service, completing, evidence)
                if retry_load:
                    raise RuntimeError("injected first durable lookup failure")
        return await lookup(**kwargs)

    async def settle(**kwargs):
        nonlocal settled
        if completing is not None:
            settled = True
            if outcome == "settle-failure":
                raise RuntimeError("injected durable settlement failure")
        return await clear(**kwargs)

    async def registration(session, *args, **kwargs):
        nonlocal registered
        if completing is not None:
            registered = True
            if outcome == "alias-failure":
                return False
        return await register(session, *args, **kwargs)

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", track)
    monkeypatch.setattr(service._durable_bridge, "lookup_retry_circuit", load)
    monkeypatch.setattr(service._durable_bridge, "clear_retry_circuit", settle)
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", registration)
    await complete(async_client, session_id="quarantine-provenance")
    assert seeded and settled and registered
    assert lookups >= 2
    assert original is not None
    entry = quarantine._http_bridge_quarantine_registry(service).get(original.key)
    if outcome != "success":
        assert entry is not None
        assert quarantine._http_bridge_session_key_poison_quarantined(service, original.key) is True
        return
    if evidence == "none":
        assert entry is None
        assert original.quarantined is False
    elif evidence == "first-strike":
        assert entry is not None
        assert entry.consecutive_eventless_timeouts == 1
        assert entry.quarantined_until == 0.0
        assert quarantine._http_bridge_session_key_poison_quarantined(service, original.key) is False
        assert original.quarantined is False
        quarantine._record_http_bridge_quarantine_eventless_timeout(service, original)
        assert entry.consecutive_eventless_timeouts == 2
        assert quarantine._http_bridge_session_key_quarantined(service, original.key) is True
    else:
        assert entry is not None
        assert entry.reason == (
            "retry_circuit_poisoned_anchor" if evidence == "poison" else "reattach_missing_response_created"
        )
        assert local_deadline == 700.0
        assert entry.quarantined_until == 700.0
        assert entry.poison_quarantined_until == (local_deadline if evidence == "poison" else 0.0)
        assert original.quarantined is True


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["poison", "first-strike", "weaker"])
@pytest.mark.parametrize("registered_owner", [False, True], ids=["detached", "registered"])
async def test_predecessor_completion_preserves_replacement_evidence(
    async_client, app_instance, promotion_transport, monkeypatch, evidence, registered_owner
):
    from dataclasses import replace

    service = get_proxy_service_for_app(app_instance)
    process = service._process_http_bridge_upstream_text
    register = service._register_http_bridge_previous_response_id
    replacement = None
    old_generation = None
    expected_generation = None
    original = None

    async def track(session, text, *args, **kwargs):
        nonlocal old_generation, original
        if json.loads(text).get("type") == "response.completed":
            original = session
            quarantine._quarantine_http_bridge_session(service, session, reason="retry_circuit_poisoned_anchor")
            old_generation = quarantine._http_bridge_quarantine_registry(service)[session.key].generation
        return await process(session, text, *args, **kwargs)

    async def replace_owner(session, *args, **kwargs):
        nonlocal replacement, expected_generation
        result = await register(session, *args, **kwargs)
        replacement = replace(session)
        quarantine._http_bridge_quarantine_registry(service).pop(session.key, None)
        if registered_owner:
            service._http_bridge_sessions[session.key] = replacement
        else:
            service._http_bridge_sessions.pop(session.key, None)
        arm(service, replacement, evidence)
        expected_generation = quarantine._http_bridge_quarantine_registry(service)[session.key].generation
        return result

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", track)
    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", replace_owner)
    try:
        await complete(async_client)
        assert replacement is not None and original is not None
        entry = quarantine._http_bridge_quarantine_registry(service).get(replacement.key)
        assert entry is not None
        assert entry.generation == expected_generation
        assert old_generation is not None
        assert expected_generation > old_generation
        assert replacement.quarantined is True
        if evidence == "first-strike":
            assert entry.consecutive_eventless_timeouts == 1
        else:
            assert quarantine._http_bridge_session_key_quarantined(service, replacement.key) is True
    finally:
        if original is not None:
            service._http_bridge_sessions[original.key] = original
