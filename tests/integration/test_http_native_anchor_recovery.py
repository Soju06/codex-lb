from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import streaming as bridge_streaming
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,  # noqa: F401
    _collect_sse_events,
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
    _get_account,
    _import_account,
    _install_bridge_settings,
    _PreviousResponseNotFoundUpstreamWebSocket,
    _replace_http_bridge_upstream_reader,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(20)]


def native_output(with_commentary):
    metadata = {"turn_id": "turn_native", "create_time": 1792706040.2}
    return [
        {"type": "reasoning", "id": "rs_native", "summary": [], "encrypted_content": "opaque-preserve-me"},
        *(
            [
                {
                    "type": "message",
                    "id": "msg_native",
                    "status": "completed",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "Checking."}],
                    "internal_chat_message_metadata_passthrough": {**metadata, "content_item_kinds": ["text"]},
                },
            ]
            if with_commentary
            else []
        ),
        {
            "type": "custom_tool_call",
            "id": "ct_native",
            "status": "completed",
            "call_id": "call_native",
            "name": "exec",
            "input": "read-only check",
            "internal_chat_message_metadata_passthrough": metadata,
        },
    ]


class NativeToolUpstream(_FakeBridgeUpstreamWebSocket):
    def __init__(self, output):
        super().__init__()
        self.output = output

    async def send_text(self, text):
        self.sent_text.append(text)
        response = {"id": "resp_native_anchor", "object": "response", "status": "in_progress"}
        events = [{"type": "response.created", "response": response}]
        for index, item in enumerate(self.output):
            for event_type in ("response.output_item.added", "response.output_item.done"):
                events.append({"type": event_type, "response_id": response["id"], "output_index": index, "item": item})
        events.append(
            {"type": "response.completed", "response": {**response, "status": "completed", "output": self.output}}
        )
        for event in events:
            await self._messages.put(_FakeUpstreamMessage("text", text=json.dumps(event)))


class RejectedAnchorUpstream(_PreviousResponseNotFoundUpstreamWebSocket):
    def __init__(self, *, after_output: bool = False):
        super().__init__()
        self.after_output = after_output

    async def send_text(self, text: str) -> None:
        if self.after_output:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text", text=json.dumps({"type": "response.reasoning_summary_text.delta", "delta": "partial"})
                )
            )
        await super().send_text(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await super().close()


@pytest.mark.parametrize("route", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("with_commentary", [False, True])
@pytest.mark.parametrize(
    ("mode", "fault"),
    [
        ("fresh", None),
        ("rejected", None),
        *[
            ("rejected", f)
            for f in (
                "missing_output",
                "missing_parallel",
                "unknown_manifest",
                "operation_fence",
                "replayed",
                "after_event",
            )
        ],
    ],
)
@pytest.mark.asyncio
async def test_native_tool_full_resend_preserves_context_and_cache(
    async_client, app_instance, monkeypatch, route, with_commentary, mode, fault
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account = await _get_account(await _import_account(async_client, "native_owner", "native-owner@example.com"))
    original_output = native_output(with_commentary)
    if with_commentary:
        original_output[-1].update(type="function_call", name="sleep", namespace="clock", arguments="{}")
        original_output[-1].pop("input")
    emitted_output = deepcopy(original_output)
    if fault == "missing_parallel":
        emitted_output.append({**original_output[-1], "id": "ct_other", "call_id": "call_omitted"})
    first = NativeToolUpstream(emitted_output)
    replacement = _FakeBridgeUpstreamWebSocket("resp_native_recovered")
    rejecting = RejectedAnchorUpstream(after_output=fault == "after_event")
    connections = []
    selections = []

    async def select(self, deadline, **kwargs):
        selections.append(kwargs)
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fresh(self, target, **kwargs):
        return target

    async def connect(headers, access_token, account_id_header, **kwargs):
        connections.append(account_id_header)
        return first if len(connections) == 1 else replacement

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", select)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    headers = {"x-codex-session-id": "native-history"}
    history = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Keep secret code ACORN-72 and check status."}],
        }
    ]
    history[0:0] = [
        {
            "type": "additional_tools",
            "id": "tools_native",
            "role": "developer",
            "tools": [{"type": "custom", "name": "exec"}],
        },
        {
            "type": "message",
            "id": "msg_control",
            "role": "developer",
            "content": [{"type": "input_text", "text": "Keep historical controls."}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_previous", "create_time": 1.0},
        },
        {"type": "compaction", "id": "cmp_native", "encrypted_content": "preserve-compaction"},
        {
            "type": "message",
            "id": "msg_control_later",
            "role": "developer",
            "content": [{"type": "input_text", "text": "Keep later historical controls too."}],
        },
    ]
    body = {
        "model": "gpt-5.1",
        "instructions": "Retain all context.",
        "input": history,
        "stream": True,
        "prompt_cache_key": "native-cache-stable",
    }
    events = await _collect_sse_events(async_client, route, json_body=body, headers=headers)
    assert events[-1]["response"]["id"] == "resp_native_anchor"
    service = get_proxy_service_for_app(app_instance)
    session = next(iter(service._http_bridge_sessions.values()))
    if mode == "fresh":
        await service._close_http_bridge_session(session)
    else:
        await _replace_http_bridge_upstream_reader(service, session, rejecting)
    full = [
        *history,
        *deepcopy(original_output),
        {
            "type": "function_call_output" if with_commentary else "custom_tool_call_output",
            "id": "cto_native",
            "call_id": "call_native",
            "output": [{"type": "input_text", "text": "status ready"}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_native", "create_time": 1792706041.0},
        },
    ]
    if fault == "missing_output":
        full.pop()
    if fault == "unknown_manifest":
        original_lookup = service._durable_bridge.lookup_request_targets

        async def unknown_manifest(**kwargs):
            lookup = await original_lookup(**kwargs)
            return replace(lookup, latest_pending_tool_calls=None) if lookup else None

        monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", unknown_manifest)
    if fault == "operation_fence":
        monkeypatch.setattr(
            bridge_streaming, "_http_bridge_verified_stale_anchor_replay_is_operation_fenced", lambda *args: False
        )
    if fault == "replayed":
        prepare = service._prepare_http_bridge_request

        def already_replayed(*args, **kwargs):
            state, text = prepare(*args, **kwargs)
            state.replay_count = 1
            return state, text

        monkeypatch.setattr(service, "_prepare_http_bridge_request", already_replayed)
    followup = {**body, "input": full}
    if route == "/v1/responses" and mode == "rejected":
        followup["previous_response_id"] = "resp_native_anchor"
    if fault:
        response = await async_client.post(route, json=followup, headers=headers)
        if response.status_code == 200:
            terminal = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ") and line[6:] != "[DONE]"
            ][-1]
            if replacement.sent_text:
                assert fault in {"missing_output", "missing_parallel", "unknown_manifest"}
                assert route == "/v1/responses"
                assert all(
                    json.loads(text).get("previous_response_id") == "resp_native_anchor"
                    for text in replacement.sent_text
                )
                return
            assert terminal["type"] == "response.failed"
        else:
            assert response.status_code in (400, 404, 502)
        assert replacement.sent_text == []
        return
    events = await _collect_sse_events(async_client, route, json_body=followup, headers=headers)
    assert events[-1]["type"] == "response.completed", events[-1]
    assert len(connections) == 2
    assert connections == [account.chatgpt_account_id] * 2
    assert len(replacement.sent_text) == 1
    replay = json.loads(replacement.sent_text[0])
    assert "previous_response_id" not in replay
    expected_input = proxy_module.ResponsesRequest.model_validate(followup).to_payload()["input"]
    assert replay["input"] == expected_input
    assert replay["prompt_cache_key"] == body["prompt_cache_key"]
    assert replay["instructions"] == body["instructions"]
    assert selections[-1]["preferred_account_id"] == account.id
    if mode == "rejected":
        assert len(rejecting.sent_text) == 1
        assert json.loads(rejecting.sent_text[0])["previous_response_id"] == "resp_native_anchor"
    next_turn = await _collect_sse_events(
        async_client,
        route,
        json_body={
            **body,
            "input": [{"role": "user", "content": "Continue on the recovered context."}],
            "previous_response_id": "resp_native_recovered",
        },
        headers=headers,
    )
    assert next_turn[-1]["type"] == "response.completed"
    assert connections == [account.chatgpt_account_id] * (2 if mode == "fresh" else 3)
    assert json.loads(replacement.sent_text[-1])["previous_response_id"] == "resp_native_recovered"
