"""Byte-compat coverage for the single-dump bridge prepare path.

``_prepare_http_bridge_request`` computes ``payload.to_payload()`` once and
threads it through client-metadata derivation, the forwarded frame and the
API-key usage budget. These tests pin that the shared dump yields exactly what
the independent per-stage dumps produced.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast

import pytest

import app.modules.proxy._service.http_bridge.streaming as bridge_streaming_module
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonObject, JsonValue
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.response_create import _response_create_client_metadata

_LITE_HEADERS = {"x-codex-turn-state": "turn-1", "user-agent": "codex_cli_rs/0.99.0"}


def _service() -> Any:
    return proxy_service.ProxyService(cast(Any, nullcontext()))


def _replayed_side_effect_input() -> list[JsonValue]:
    """Two replays of one ``write_stdin`` call: the dedupe branch removes the second call."""
    arguments = json.dumps({"session_id": 75180, "chars": "", "yield_time_ms": 30000, "max_output_tokens": 22000})
    return [
        {"type": "function_call", "name": "write_stdin", "arguments": arguments, "call_id": "call_first"},
        {"type": "function_call_output", "call_id": "call_first", "output": "Process running with session ID 75180"},
        {"type": "reasoning", "summary": []},
        {
            "type": "function_call",
            "name": "write_stdin",
            "arguments": json.dumps(
                {"session_id": 75180, "chars": "", "yield_time_ms": 30000, "max_output_tokens": 4000}
            ),
            "call_id": "call_replay",
        },
        {"type": "function_call_output", "call_id": "call_replay", "output": "Process exited with code 0"},
    ]


_CASES: dict[str, tuple[dict[str, JsonValue], dict[str, str], bool]] = {
    # (request body, headers, preserve_responses_lite_client_metadata)
    "plain-client-metadata": (
        {
            "model": "gpt-5.6",
            "instructions": "be brief 한국어",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "héllo"}]}],
            "client_metadata": {"x-codex-turn-metadata": '{"turn_id":"t1"}', "caller": "stable"},
            "stream": True,
            "reasoning": {"effort": "medium"},
        },
        _LITE_HEADERS,
        False,
    ),
    "client-metadata-dropped": (
        {
            "model": "gpt-5.6",
            "instructions": "",
            "input": "plain string input",
            "client_metadata": {"x-codex-installation-id": "client-installation"},
            "stream": True,
            "background": False,
        },
        {},
        False,
    ),
    "sanitized-input": (
        {
            "model": "gpt-5.6",
            "instructions": "x" * 9000,
            "input": [
                {"role": "developer", "content": "sys"},
                {"type": "reasoning", "summary": [], "encrypted_content": "abc"},
                {
                    "type": "function_call",
                    "name": "multi_agent_v1__tool_search",
                    "arguments": "{}",
                    "call_id": "call_ns",
                },
                {"type": "function_call_output", "call_id": "call_ns", "output": "ok"},
                {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
            ],
            "temperature": 0.2,
            "truncation": None,
            "stream": True,
        },
        {"x-codex-session-id": "sid-1"},
        False,
    ),
    "responses-lite": (
        {
            "model": "gpt-5.6",
            "instructions": "lite",
            "input": [
                {"type": "additional_tools", "tools": [{"type": "function", "name": "shell"}]},
                {"role": "user", "content": "go"},
            ],
            "client_metadata": {"x-codex-responses-lite-websocket": "true"},
            "reasoning": {"effort": "high", "summary": "auto"},
        },
        _LITE_HEADERS,
        True,
    ),
    "previous-response-dedupe": (
        {
            "model": "gpt-5.6",
            "instructions": "continue",
            "input": _replayed_side_effect_input(),
            "previous_response_id": "resp_parent",
            "client_metadata": {"caller": "stable"},
        },
        _LITE_HEADERS,
        False,
    ),
}


def _prepare_state(
    service: Any,
    payload: ResponsesRequest,
    *,
    headers: dict[str, str],
    preserve_lite: bool,
    upstream_payload_base: JsonObject | None,
) -> tuple[Any, str]:
    return service._prepare_response_bridge_request_state(
        payload,
        api_key=None,
        api_key_reservation=None,
        include_type_field=True,
        attach_event_queue=False,
        transport="http",
        client_metadata=_response_create_client_metadata(
            payload.to_payload(),
            headers=headers,
            preserve_existing_responses_lite=preserve_lite,
        ),
        headers=headers,
        request_id="ws_fixed",
        request_log_id="req_fixed",
        upstream_payload_base=upstream_payload_base,
    )


def _comparable(request_state: Any) -> dict[str, object]:
    return {
        "request_text": request_state.request_text,
        "input_full_fingerprint": request_state.input_full_fingerprint,
        "input_item_count": request_state.input_item_count,
        "request_usage_budget": request_state.request_usage_budget,
        "service_tier": request_state.service_tier,
        "previous_response_id": request_state.previous_response_id,
        "proxy_injected_anchor_had_full_resend_payload": request_state.proxy_injected_anchor_had_full_resend_payload,
    }


@pytest.mark.parametrize("case", sorted(_CASES))
def test_prepare_state_with_shared_dump_matches_independent_dumps(case: str) -> None:
    body, headers, preserve_lite = _CASES[case]
    service = _service()
    payload = ResponsesRequest.model_validate(body)
    base_payload = payload.to_payload()
    pristine_base = json.dumps(base_payload, sort_keys=True)

    fresh_state, fresh_text = _prepare_state(
        service, payload, headers=headers, preserve_lite=preserve_lite, upstream_payload_base=None
    )
    shared_state, shared_text = _prepare_state(
        service, payload, headers=headers, preserve_lite=preserve_lite, upstream_payload_base=base_payload
    )

    assert shared_text == fresh_text
    assert _comparable(shared_state) == _comparable(fresh_state)
    assert json.dumps(base_payload, sort_keys=True) == pristine_base, "the shared dump must stay pristine"


def test_prepare_state_dedupe_branch_discards_caller_dump_and_forwards_deduped_input() -> None:
    body, headers, preserve_lite = _CASES["previous-response-dedupe"]
    service = _service()
    payload = ResponsesRequest.model_validate(body)
    base_payload = payload.to_payload()
    assert sum(1 for item in cast(list[Any], base_payload["input"]) if item.get("type") == "function_call") == 2

    request_state, text_data = _prepare_state(
        service, payload, headers=headers, preserve_lite=preserve_lite, upstream_payload_base=base_payload
    )

    forwarded = json.loads(text_data)
    forwarded_calls = [item for item in forwarded["input"] if item.get("type") == "function_call"]
    assert [item["call_id"] for item in forwarded_calls] == ["call_first"], "un-deduped caller dump leaked"
    assert forwarded["previous_response_id"] == "resp_parent"
    assert request_state.input_item_count == len(_replayed_side_effect_input())
    assert request_state.request_usage_budget.input_tokens is None


@pytest.mark.parametrize(
    ("case", "expected_calls"),
    [
        pytest.param("plain-client-metadata", 1, id="common-path"),
        pytest.param("client-metadata-dropped", 1, id="metadata-dropped"),
        pytest.param("sanitized-input", 1, id="sanitized-input"),
        pytest.param("responses-lite", 1, id="responses-lite"),
        pytest.param("previous-response-dedupe", 2, id="dedupe-recomputes-once"),
    ],
)
def test_prepare_http_bridge_request_dumps_payload_at_most_twice(
    monkeypatch: pytest.MonkeyPatch, case: str, expected_calls: int
) -> None:
    body, headers, preserve_lite = _CASES[case]
    service = _service()
    payload = ResponsesRequest.model_validate(body)
    original_to_payload = ResponsesRequest.to_payload
    calls: list[int] = []

    def counting_to_payload(self: ResponsesRequest) -> JsonObject:
        calls.append(id(self))
        return original_to_payload(self)

    monkeypatch.setattr(ResponsesRequest, "to_payload", counting_to_payload)

    request_state, text_data = service._prepare_http_bridge_request(
        payload,
        headers,
        api_key=None,
        api_key_reservation=None,
        request_id="req_fixed",
        preserve_responses_lite_client_metadata=preserve_lite,
    )

    assert len(calls) == expected_calls
    assert request_state.request_text == text_data
    assert json.loads(text_data)["type"] == "response.create"


def test_prepare_http_bridge_request_matches_legacy_per_stage_dumps() -> None:
    """End-to-end: the public prepare entry point emits the same frame the per-stage dumps did."""
    for case, (body, headers, preserve_lite) in _CASES.items():
        service = _service()
        payload = ResponsesRequest.model_validate(body)

        request_state, text_data = service._prepare_http_bridge_request(
            payload,
            headers,
            api_key=None,
            api_key_reservation=None,
            request_id="req_fixed",
            preserve_responses_lite_client_metadata=preserve_lite,
        )
        legacy_state, legacy_text = _prepare_state(
            service, payload, headers=headers, preserve_lite=preserve_lite, upstream_payload_base=None
        )

        assert text_data == legacy_text, case
        comparable = _comparable(request_state)
        legacy = _comparable(legacy_state)
        assert comparable == legacy, case


@pytest.mark.asyncio
async def test_stream_http_bridge_or_retry_shares_size_gate_dump_with_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    settings = SimpleNamespace(upstream_stream_transport="auto", max_sse_event_bytes=16 * 1024 * 1024)

    class _SettingsCache:
        async def get(self) -> object:
            return SimpleNamespace(upstream_stream_transport="default")

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        bridge_streaming_module,
        "_http_bridge_runtime_config",
        lambda _dashboard_settings, _app_settings: proxy_service._HTTPBridgeRuntimeConfig(
            enabled=True,
            idle_ttl_seconds=30.0,
            codex_idle_ttl_seconds=30.0,
            max_sessions=8,
            queue_limit=16,
            prompt_cache_idle_ttl_seconds=30.0,
            gateway_safe_mode=False,
        ),
    )
    monkeypatch.setattr(bridge_streaming_module, "upstream_websocket_transport_recently_failed", lambda: False)

    async def resolve_none(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(service, "_resolve_forwarded_file_account_for_responses", resolve_none)
    captured: dict[str, Any] = {}

    async def fake_stream_via_http_bridge(payload: ResponsesRequest, headers: Any, **kwargs: Any):
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        yield "data: bridge\n\n"

    monkeypatch.setattr(service, "_stream_via_http_bridge", fake_stream_via_http_bridge)
    payload = ResponsesRequest.model_validate(_CASES["plain-client-metadata"][0])

    output = [
        line
        async for line in service._stream_http_bridge_or_retry(
            payload,
            {},
            codex_session_affinity=False,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
        )
    ]

    assert output == ["data: bridge\n\n"]
    assert captured["payload"] is payload
    assert captured["kwargs"]["bridge_payload"] == payload.to_payload()
