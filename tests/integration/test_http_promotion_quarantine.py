from __future__ import annotations

import json

import pytest

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import quarantine
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as cleanup_http_bridge_sessions,  # noqa: F401
)
from tests.integration.test_http_responses_bridge import (
    _collect_sse_events,
    _promotion_history,
)
from tests.integration.test_http_responses_bridge import (
    promotion_transport as promotion_transport,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["weaker", "poison", "first-strike"])
@pytest.mark.parametrize("during_completion", [False, True], ids=["after-completion", "during-completion"])
async def test_promoted_history_quarantine_opens_fresh_without_injecting_an_anchor(
    async_client, app_instance, promotion_transport, evidence, during_completion, monkeypatch
):
    upstreams, raw_calls, _ = promotion_transport
    history = _promotion_history()
    body = {"model": "gpt-5.4", "instructions": "test", "stream": True, "input": history}
    service = get_proxy_service_for_app(app_instance)
    reason = (
        quarantine._HTTP_BRIDGE_QUARANTINE_POISONED_ANCHOR_REASON
        if evidence == "poison"
        else quarantine._HTTP_BRIDGE_QUARANTINE_WEDGED_REATTACH_REASON
    )
    register = service._register_http_bridge_previous_response_id
    armed = False

    async def register_and_arm(session, *args, **kwargs):
        nonlocal armed
        result = await register(session, *args, **kwargs)
        if not armed:
            armed = True
            if evidence == "first-strike":
                quarantine._record_http_bridge_quarantine_eventless_timeout(service, session)
            else:
                quarantine._quarantine_http_bridge_session(service, session, reason=reason)
        return result

    if during_completion:
        monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_and_arm)
    first = await _collect_sse_events(async_client, "/v1/responses", json_body=body)
    assert first[-1]["type"] == "response.completed"
    assert len(service._http_bridge_sessions) == 1
    original = next(iter(service._http_bridge_sessions.values()))
    assert original.key.strength == "soft"
    assert original.key.affinity_key.startswith("http-history:")
    if not during_completion:
        if evidence == "first-strike":
            quarantine._record_http_bridge_quarantine_eventless_timeout(service, original)
        else:
            quarantine._quarantine_http_bridge_session(service, original, reason=reason)
    if evidence == "first-strike":
        entry = quarantine._http_bridge_quarantine_registry(service).get(original.key)
        assert entry is not None, "completion must preserve a first strike recorded during alias persistence"
        assert entry.consecutive_eventless_timeouts == 1
        quarantine._record_http_bridge_quarantine_eventless_timeout(service, original)
    assert quarantine._http_bridge_session_key_quarantined(service, original.key)

    followup = [*history, {"role": "assistant", "content": "OK"}, {"role": "user", "content": "next"}]
    second = await _collect_sse_events(async_client, "/v1/responses", json_body={**body, "input": followup})
    assert second[-1]["type"] == "response.completed"
    assert not raw_calls
    assert len(upstreams) == 2
    assert service._http_bridge_sessions[original.key] is not original
    assert len(upstreams[0].sent_text) == 1
    frame = json.loads(upstreams[1].sent_text[0])
    assert frame["input"] == [
        history[0],
        {"role": "assistant", "content": [{"type": "output_text", "text": "first answer"}]},
        history[2],
        {"role": "assistant", "content": [{"type": "output_text", "text": "OK"}]},
        followup[-1],
    ]
    assert "previous_response_id" not in frame
