from __future__ import annotations

import json

import pytest

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import quarantine
from tests.integration.http_quarantine_fixtures import arm
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
@pytest.mark.parametrize("capture_stage", ["registration", "preload"])
async def test_promoted_history_quarantine_opens_fresh_without_injecting_an_anchor(
    async_client, app_instance, promotion_transport, evidence, during_completion, capture_stage, monkeypatch
):
    upstreams, raw_calls, _ = promotion_transport
    history = _promotion_history()
    body = {"model": "gpt-5.4", "instructions": "test", "stream": True, "input": history}
    service = get_proxy_service_for_app(app_instance)
    hook_name = (
        "_register_http_bridge_previous_response_id"
        if capture_stage == "registration"
        else "_load_http_bridge_retry_circuit"
    )
    register = getattr(service, hook_name)
    armed = False
    processing_completion = False
    process = service._process_http_bridge_upstream_text

    async def process_and_track(session, text, *args, **kwargs):
        nonlocal processing_completion
        processing_completion = json.loads(text).get("type") == "response.completed"
        try:
            return await process(session, text, *args, **kwargs)
        finally:
            processing_completion = False

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", process_and_track)

    async def register_and_arm(session, *args, **kwargs):
        nonlocal armed
        result = await register(session, *args, **kwargs)
        if not armed and (capture_stage == "registration" or processing_completion):
            armed = True
            arm(service, session, evidence)
        return result

    if during_completion:
        monkeypatch.setattr(service, hook_name, register_and_arm)
    first = await _collect_sse_events(async_client, "/v1/responses", json_body=body)
    assert first[-1]["type"] == "response.completed"
    if during_completion:
        assert armed, "hook must inject evidence during completion"
    assert len(service._http_bridge_sessions) == 1
    original = next(iter(service._http_bridge_sessions.values()))
    assert original.key.strength == "soft"
    assert original.key.affinity_key.startswith("http-history:")
    if not during_completion:
        arm(service, original, evidence)
    if evidence == "first-strike":
        entry = quarantine._http_bridge_quarantine_registry(service).get(original.key)
        assert entry is not None, (
            "completion must preserve a first strike recorded during the selected completion await"
        )
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
