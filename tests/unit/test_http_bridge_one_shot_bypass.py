"""Tool-less, self-contained one-shot requests bypass the HTTP bridge."""

from __future__ import annotations

import pytest

from app.core.openai.requests import ResponsesRequest
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


def test_agent_turns_with_tools_are_not_one_shot() -> None:
    payload = _payload(tools=[{"type": "function", "name": "bash", "parameters": {"type": "object"}}])
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
