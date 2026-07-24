"""Tool-less, self-contained one-shot requests bypass the HTTP bridge."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.openai.requests import ResponsesRequest
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import helpers as http_bridge_helpers_module

pytestmark = pytest.mark.unit

_OPENCODE_HEADERS = {
    "user-agent": "opencode/1.18.3 (darwin arm64)",
    "x-session-affinity": "ses_side_call",
    "x-session-id": "ses_side_call",
}


def _payload(**extra: object) -> ResponsesRequest:
    return ResponsesRequest.model_validate(
        {"model": "gpt-5.6-sol", "instructions": "t", "input": "Generate a title for this conversation:", **extra}
    )


def _is_one_shot(payload: ResponsesRequest, headers: dict[str, str], *, forwarded_request: bool = False) -> bool:
    return http_bridge_helpers_module._http_bridge_request_is_unanchored_one_shot(
        payload,
        headers,
        forwarded_request=forwarded_request,
    )


def test_tool_less_side_call_is_one_shot() -> None:
    assert _is_one_shot(_payload(), _OPENCODE_HEADERS)
    assert _is_one_shot(_payload(tools=[]), _OPENCODE_HEADERS)


def test_empty_tool_map_side_call_is_one_shot() -> None:
    # OpenCode's title/compaction side calls declare tool-lessness with an
    # empty tool map (``tools: {}``) on the wire; request validation
    # normalizes that shape to ``[]`` so these payloads reach the bypass.
    assert _is_one_shot(_payload(tools={}), _OPENCODE_HEADERS)


def test_agent_turns_with_tools_are_not_one_shot() -> None:
    payload = _payload(tools=[{"type": "function", "name": "bash", "parameters": {"type": "object"}}])
    assert not _is_one_shot(payload, _OPENCODE_HEADERS)


def test_responses_lite_agent_turns_with_tools_are_not_one_shot() -> None:
    payload = _payload(
        input=[
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [{"type": "function", "name": "bash", "parameters": {"type": "object"}}],
            },
            {"role": "user", "content": "Run the command"},
        ]
    )
    assert not _is_one_shot(payload, _OPENCODE_HEADERS)


def test_continuity_bearing_requests_are_not_one_shot() -> None:
    assert not _is_one_shot(_payload(previous_response_id="resp_1"), _OPENCODE_HEADERS)
    assert not _is_one_shot(_payload(conversation="conv_1"), _OPENCODE_HEADERS)
    assert not _is_one_shot(
        _payload(),
        {**_OPENCODE_HEADERS, "x-codex-turn-state": "turn-state-token"},
    )


def test_file_pinned_requests_are_not_one_shot() -> None:
    payload = _payload(
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_file", "file_id": "file-abc123"}],
            }
        ]
    )
    assert not _is_one_shot(payload, _OPENCODE_HEADERS)


def test_native_codex_clients_are_excluded() -> None:
    headers = {**_OPENCODE_HEADERS, "originator": "codex_cli_rs"}
    assert not _is_one_shot(_payload(), headers)


def test_forwarded_requests_are_excluded() -> None:
    assert not _is_one_shot(_payload(), _OPENCODE_HEADERS, forwarded_request=True)


def test_requests_without_session_identity_keep_bridge_behavior() -> None:
    assert not _is_one_shot(_payload(), {})
    assert not _is_one_shot(_payload(), {"user-agent": "opencode/1.18.3"})


def test_codex_name_session_headers_keep_bridge_behavior() -> None:
    # Codex-name identity means a bridge-centric Codex-protocol flow, even
    # when the payload happens to be tool-less.
    assert not _is_one_shot(_payload(), {"session_id": "sid_codex"})
    assert not _is_one_shot(_payload(), {"thread-id": "thread_codex"})
    assert not _is_one_shot(
        _payload(),
        {**_OPENCODE_HEADERS, "session_id": "sid_codex"},
    )


@pytest.mark.asyncio
async def test_dashboard_websocket_override_keeps_one_shot_on_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    base_settings = proxy_service.get_settings().model_copy(update={"upstream_stream_transport": "auto"})
    dashboard_settings = base_settings.model_copy(update={"upstream_stream_transport": "websocket"})
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: SimpleNamespace(get=AsyncMock(return_value=dashboard_settings)),
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: base_settings)
    monkeypatch.setattr(
        proxy_service,
        "_http_bridge_runtime_config",
        lambda _dashboard, _base: proxy_service._HTTPBridgeRuntimeConfig(
            enabled=True,
            idle_ttl_seconds=30.0,
            codex_idle_ttl_seconds=30.0,
            max_sessions=8,
            queue_limit=16,
            prompt_cache_idle_ttl_seconds=30.0,
            gateway_safe_mode=False,
        ),
    )
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))

    async def stream_via_bridge(*_args: object, **_kwargs: object):
        yield "data: bridge\n\n"

    async def forbidden_retry(*_args: object, **_kwargs: object):
        raise AssertionError("explicit websocket transport must keep the bridge")
        yield "data: retry\n\n"

    monkeypatch.setattr(service, "_stream_via_http_bridge", stream_via_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", forbidden_retry)

    output = [
        line
        async for line in service._stream_http_bridge_or_retry(
            _payload(),
            _OPENCODE_HEADERS,
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
        )
    ]

    assert output == ["data: bridge\n\n"]
