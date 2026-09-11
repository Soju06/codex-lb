from __future__ import annotations

import json

import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import quarantine, streaming, upstream_events
from tests.integration.test_http_responses_bridge import (
    test_backend_responses_http_bridge_replays_verified_full_resend_after_stale_owner as replay_scenario,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_poison", [False, True], ids=["captured-absence", "captured-poison"])
async def test_verified_replay_retains_first_strike_after_origin_capture(
    async_client, app_instance, monkeypatch, initial_poison
):
    capture = streaming._http_bridge_quarantine_clear_fence
    process = proxy_service.ProxyService._process_http_bridge_upstream_text
    local_fence = streaming._http_bridge_local_failure_fence
    clear = upstream_events._clear_http_bridge_quarantine
    source_key = None
    injected = False
    cleared = False
    observations = []

    def track_clear(service, session, **kwargs):
        nonlocal cleared
        cleared = True
        return clear(service, session, **kwargs)

    def prepare_capture(service):
        if initial_poison:
            assert len(service._http_bridge_sessions) == 1
            source = next(iter(service._http_bridge_sessions.values()))
            quarantine._quarantine_http_bridge_session(service, source, reason="retry_circuit_poisoned_anchor")
        return local_fence(service)

    def capture_then_strike(service, key):
        nonlocal source_key, injected
        source_key = key
        source = service._http_bridge_sessions.get(key)
        assert source is not None
        if initial_poison and not quarantine._http_bridge_session_key_poison_quarantined(service, key):
            quarantine._quarantine_http_bridge_session(service, source, reason="retry_circuit_poisoned_anchor")
        result = capture(service, key)
        if not initial_poison:
            assert result is None
        quarantine._record_http_bridge_quarantine_eventless_timeout(service, source)
        injected = True
        # The reused transport scenario spies on cleanup. Execute real cleanup
        # for this proof; an untouched source entry would otherwise pass it.
        monkeypatch.setattr(upstream_events, "_clear_http_bridge_quarantine", track_clear)
        return result

    async def check_replay(service, session, text, *args, **kwargs):
        replay = None
        if json.loads(text).get("type") == "response.completed":
            replay = next((state for state in session.pending_requests if state.verified_stale_anchor_replay), None)
        result = await process(service, session, text, *args, **kwargs)
        if replay is not None:
            assert injected and source_key is not None
            assert replay.verified_stale_anchor_retry_circuit_key == source_key
            entry = quarantine._http_bridge_quarantine_registry(service).get(source_key)
            observations.append(
                None
                if entry is None
                else (
                    entry.consecutive_eventless_timeouts,
                    entry.quarantined_until,
                    quarantine._http_bridge_session_key_poison_quarantined(service, source_key),
                )
            )
        return result

    monkeypatch.setattr(streaming, "_http_bridge_local_failure_fence", prepare_capture)
    monkeypatch.setattr(streaming, "_http_bridge_quarantine_clear_fence", capture_then_strike)
    monkeypatch.setattr(proxy_service.ProxyService, "_process_http_bridge_upstream_text", check_replay)
    await replay_scenario(async_client, app_instance, monkeypatch, "account-neutral")
    assert injected and cleared
    assert observations == [(1, 0.0, False)]
