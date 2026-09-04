from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

import app.modules.proxy._service.websocket.mixin as ws_mixin
from app.core.types import JsonValue
from app.modules.proxy import service as proxy_service
from tests.unit.test_proxy_utils import (
    _make_proxy_settings,
    _repo_factory,
    _RequestLogsRecorder,
    _SettingsCache,
)
from tests.unit.test_proxy_websocket_model_source_guard import _api_key, _create_frame, _Downstream

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("controls", [{"reasoning": {"effort": "none"}}, {"top_logprobs": 2}])
async def test_astra_source_controls_reach_websocket_http_fallback(monkeypatch, controls):
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", AsyncMock(side_effect=[True, False]))
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    select = AsyncMock(side_effect=AssertionError("Source request reached subscription account selection"))
    monkeypatch.setattr(service, "_select_websocket_connect_account", select)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))
    frame = json.loads(_create_frame("gpt-6-astra"))
    frame.update(controls)
    socket = _Downstream([json.dumps(frame)])

    await asyncio.wait_for(
        service.proxy_responses_websocket(
            cast(WebSocket, socket), {}, codex_session_affinity=False, openai_cache_affinity=False, api_key=None
        ),
        timeout=5,
    )

    events = [json.loads(text) for text in socket.sent_text]
    assert any(event.get("error", {}).get("code") == "model_source_requires_http_transport" for event in events), events
    assert any(event.get("status") == 503 for event in events), events
    select.assert_not_awaited()


@pytest.mark.parametrize(
    "key",
    [
        replace(_api_key(), allowed_reasoning_efforts=["low"]),
        replace(_api_key(), enforced_reasoning_effort="low"),
    ],
    ids=["allow-only-low", "enforced-low"],
)
async def test_astra_websocket_rejects_high_configuration_update_before_upstream(monkeypatch, key):
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", AsyncMock(return_value=False))
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=key))
    reserve = AsyncMock(side_effect=AssertionError("Rejected request reached API-key reservation"))
    select = AsyncMock(side_effect=AssertionError("Rejected request reached upstream account selection"))
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve)
    monkeypatch.setattr(service, "_select_websocket_connect_account", select)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))
    frame = json.loads(_create_frame("gpt-6-astra"))
    frame["input"].insert(0, {"type": "configuration_update", "reasoning": {"effort": "high"}})
    socket = _Downstream([json.dumps(frame)])

    await asyncio.wait_for(
        service.proxy_responses_websocket(
            cast(WebSocket, socket), {}, codex_session_affinity=False, openai_cache_affinity=False, api_key=key
        ),
        timeout=5,
    )

    events = [json.loads(text) for text in socket.sent_text]
    rejection = next(event for event in events if event.get("error", {}).get("code") == "reasoning_effort_not_allowed")
    assert rejection["type"] == "error"
    assert rejection["status"] == 403
    assert rejection["error"]["type"] == "permission_error"
    reserve.assert_not_awaited()
    select.assert_not_awaited()


@pytest.mark.parametrize("client_anchor", [False, True])
async def test_astra_websocket_final_anchor_resets_effort_before_reservation(monkeypatch, client_anchor):
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(ws_mixin, "responses_model_is_source_owned", AsyncMock(return_value=False))
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    key = replace(_api_key(), enforced_reasoning_effort="low")
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=key))
    reserve = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve)
    history: list[JsonValue] = [{"role": "user", "content": [{"type": "input_text", "text": "Earlier"}]}]
    new_input: dict[str, JsonValue] = {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]}
    continuity = proxy_service._WebSocketContinuityState(
        last_completed_input_count=len(history),
        last_completed_response_id="resp_inherited_high",
        last_completed_input_prefix_fingerprint=proxy_service._fingerprint_input_items(history),
    )
    payload: dict[str, JsonValue] = {"type": "response.create", "model": "gpt-6-astra", "input": [*history, new_input]}
    if client_anchor:
        payload["previous_response_id"] = "resp_inherited_high"
        payload["input"] = [new_input]

    prepared = await service._prepare_websocket_response_create_request(
        payload,
        headers={"session_id": "astra-policy"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=key,
        continuity_state=continuity,
    )

    forwarded = json.loads(prepared.text_data)
    assert forwarded["previous_response_id"] == "resp_inherited_high"
    assert forwarded["input"] == [{"type": "configuration_update", "reasoning": {"effort": "low"}}, new_input]
    assert prepared.request_state.proxy_injected_previous_response_id is not client_anchor
    reserve.assert_awaited_once()
