from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

import app.modules.proxy.api as proxy_api_module

pytestmark = pytest.mark.unit


async def _iter_blocks(*blocks: str) -> AsyncIterator[str]:
    for block in blocks:
        yield block


@pytest.mark.asyncio
async def test_collect_responses_payload_returns_contract_error_on_truncated_stream() -> None:
    result = await proxy_api_module._collect_responses_payload(
        _iter_blocks('data: {"type":"response.output_text.delta","delta":"hello"}\n\n')
    )

    body = result.model_dump(mode="json", exclude_none=True)
    assert body["error"]["code"] == "upstream_stream_truncated"


@pytest.mark.asyncio
async def test_collect_responses_payload_normalizes_unknown_output_item_to_message() -> None:
    result = await proxy_api_module._collect_responses_payload(
        _iter_blocks(
            (
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"id":"fa_1","type":"final_answer","text":"hello from final answer"}}\n\n'
            ),
            (
                'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
                '"status":"completed","output":[]}}\n\n'
            ),
        )
    )

    body = result.model_dump(mode="json", exclude_none=True)
    assert body["id"] == "resp_1"
    assert body["output"] == [
        {
            "id": "fa_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "hello from final answer"}],
        }
    ]


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_appends_response_failed_on_invalid_json() -> None:
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(_iter_blocks("data: {not-json}\n\n"))
    ]

    assert len(blocks) == 1
    payload = proxy_api_module._parse_sse_payload(blocks[0])
    assert payload is not None
    assert payload["type"] == "response.failed"
    response = payload["response"]
    assert isinstance(response, dict)
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == "invalid_json"


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_normalizes_unknown_terminal_output_item() -> None:
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":1,"response":{"id":"resp_1",'
                    '"object":"response",'
                    '"status":"completed","output":[{"id":"fa_1","type":"final_answer","text":"normalized"}]}}\n\n'
                )
            )
        )
    ]

    # Now: response.created, synthetic delta, response.completed
    assert len(blocks) == 3
    created_payload = proxy_api_module._parse_sse_payload(blocks[0])
    assert created_payload is not None
    assert created_payload["type"] == "response.created"
    delta_payload = proxy_api_module._parse_sse_payload(blocks[1])
    assert delta_payload is not None
    assert delta_payload["type"] == "response.output_text.delta"
    assert delta_payload["delta"] == "normalized"
    payload = proxy_api_module._parse_sse_payload(blocks[2])
    assert payload is not None
    assert payload["type"] == "response.completed"
    response = payload["response"]
    assert isinstance(response, dict)
    output = response["output"]
    assert isinstance(output, list)
    assert output == [
        {
            "id": "fa_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "normalized"}],
        }
    ]


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_synthesizes_delta_from_done_message() -> None:
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.output_item.done","sequence_number":1,"output_index":0,'
                    '"item":{"id":"msg_1","type":"message","role":"assistant",'
                    '"content":[{"type":"output_text","text":"visible text"}]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":2,"response":{"id":"resp_1",'
                    '"object":"response",'
                    '"status":"completed","output":[]}}\n\n'
                ),
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(block) for block in blocks]
    assert payloads[0] is not None and payloads[0]["type"] == "response.created"
    assert payloads[1] == {
        "type": "response.output_text.delta",
        "output_index": 0,
        "content_index": 0,
        "delta": "visible text",
        "item_id": "msg_1",
    }
    assert payloads[2] is not None
    assert payloads[2]["type"] == "response.output_item.done"
    assert payloads[3] is not None
    assert payloads[3]["type"] == "response.completed"


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_synthesizes_delta_from_completed_output() -> None:
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":1,"response":{"id":"resp_1",'
                    '"object":"response",'
                    '"status":"completed","output":[{"id":"msg_1","type":"message",'
                    '"content":[{"type":"output_text","text":"terminal text"}]}]}}\n\n'
                )
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(block) for block in blocks]
    assert payloads[0] is not None and payloads[0]["type"] == "response.created"
    assert payloads[1] == {
        "type": "response.output_text.delta",
        "output_index": 0,
        "content_index": 0,
        "delta": "terminal text",
        "item_id": "msg_1",
    }
    assert payloads[2] is not None
    assert payloads[2]["type"] == "response.completed"


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_does_not_duplicate_existing_delta() -> None:
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                'data: {"type":"response.output_text.delta","sequence_number":1,"item_id":"msg_1",'
                '"delta":"already visible"}\n\n',
                (
                    'data: {"type":"response.output_item.done","sequence_number":2,"output_index":0,'
                    '"item":{"id":"msg_1","type":"message","role":"assistant",'
                    '"content":[{"type":"output_text","text":"already visible"}]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":3,"response":{"id":"resp_1",'
                    '"object":"response",'
                    '"status":"completed","output":[]}}\n\n'
                ),
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(block) for block in blocks]
    event_types = [payload["type"] for payload in payloads if payload is not None]
    assert event_types == [
        "response.created",
        "response.output_text.delta",
        "response.output_item.done",
        "response.completed",
    ]


@pytest.mark.asyncio
async def test_collect_responses_payload_preserves_apply_patch_call_output_item() -> None:
    result = await proxy_api_module._collect_responses_payload(
        _iter_blocks(
            (
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"id":"apc_1","type":"apply_patch_call","status":"completed",'
                '"call_id":"call_1","patch":"*** Begin Patch\\n*** End Patch\\n"}}\n\n'
            ),
            (
                'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
                '"status":"completed","output":[]}}\n\n'
            ),
        )
    )

    body = result.model_dump(mode="json", exclude_none=True)
    assert body["id"] == "resp_1"
    assert body["output"] == [
        {
            "id": "apc_1",
            "type": "apply_patch_call",
            "status": "completed",
            "call_id": "call_1",
            "patch": "*** Begin Patch\n*** End Patch\n",
        }
    ]


@pytest.mark.asyncio
async def test_collect_responses_payload_preserves_mcp_approval_request_output_item() -> None:
    result = await proxy_api_module._collect_responses_payload(
        _iter_blocks(
            (
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"id":"mcp_1","type":"mcp_approval_request","status":"in_progress",'
                '"request_id":"req_1","server_label":"github","tool_name":"repos/list"}}\n\n'
            ),
            (
                'data: {"type":"response.completed","response":{"id":"resp_2","object":"response",'
                '"status":"completed","output":[]}}\n\n'
            ),
        )
    )

    body = result.model_dump(mode="json", exclude_none=True)
    assert body["id"] == "resp_2"
    assert body["output"] == [
        {
            "id": "mcp_1",
            "type": "mcp_approval_request",
            "status": "in_progress",
            "request_id": "req_1",
            "server_label": "github",
            "tool_name": "repos/list",
        }
    ]


@pytest.mark.asyncio
async def test_collect_responses_payload_preserves_output_image_item() -> None:
    result = await proxy_api_module._collect_responses_payload(
        _iter_blocks(
            (
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"id":"img_1","type":"output_image","image_url":"https://example.com/a.png"}}\n\n'
            ),
            (
                'data: {"type":"response.completed","response":{"id":"resp_3","object":"response",'
                '"status":"completed","output":[]}}\n\n'
            ),
        )
    )

    body = result.model_dump(mode="json", exclude_none=True)
    assert body["id"] == "resp_3"
    assert body["output"] == [
        {
            "id": "img_1",
            "type": "output_image",
            "image_url": "https://example.com/a.png",
        }
    ]


# --- OpenAI SDK stream contract regressions ---
# These tests cover the public /v1/responses streaming SSE contract gaps found
# during the OpenAI Python SDK compatibility audit. See change
# `normalize-v1-responses-openai-sdk-stream` in openspec/changes/ for the
# full audit, design rationale, and spec delta.


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_drops_codex_rate_limits_prefix() -> None:
    """G1: Codex-internal vendor events MUST NOT leak onto the public /v1 stream.

    The upstream Codex backend emits `codex.rate_limits` (throttled per window)
    before `response.created`. The OpenAI SDK's ResponseStreamState raises
    `RuntimeError: Expected to have received 'response.created' before
    'codex.rate_limits'` on the first event. The /v1 normalizer must drop any
    `codex.*` event so the first event the public stream emits is
    `response.created`.
    """
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                'data: {"type":"codex.rate_limits","plan_type":"pro","rate_limits":{"allowed":true}}\n\n',
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":1,'
                    '"response":{"id":"resp_1","object":"response","status":"completed",'
                    '"output":[{"id":"msg_1","type":"message","role":"assistant","status":"completed",'
                    '"content":[{"type":"output_text","text":"hi"}]}]}}\n\n'
                ),
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(b) for b in blocks]
    event_types = [p["type"] for p in payloads if p is not None]
    assert "codex.rate_limits" not in event_types
    # First event MUST be response.created (OpenAI SDK contract A)
    standard_events = [t for t in event_types if t not in ("[DONE]",)]
    assert standard_events[0] == "response.created"


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_backfills_terminal_output_from_items() -> None:
    """G3: terminal `response.completed.output` MUST be backfilled from
    `response.output_item.done` events when upstream sends empty output.

    The Codex backend emits items via `output_item.done` and then sends
    `response.completed` with `output: []`. The non-streaming path
    (`_collect_responses_payload`) already merges these; the streaming path
    must do the same so OpenAI SDK consumers calling
    `stream.get_final_response().output` see the items.
    """
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.output_item.done","sequence_number":1,"output_index":0,'
                    '"item":{"id":"msg_1","type":"message","role":"assistant","status":"completed",'
                    '"content":[{"type":"output_text","text":"backfilled"}]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":2,'
                    '"response":{"id":"resp_1","object":"response","status":"completed","output":[]}}\n\n'
                ),
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(b) for b in blocks]
    completed = next(p for p in payloads if p and p.get("type") == "response.completed")
    response_obj = completed["response"]
    assert isinstance(response_obj, dict)
    output = response_obj["output"]
    assert isinstance(output, list)
    assert len(output) == 1
    assert output[0]["id"] == "msg_1"
    assert output[0]["type"] == "message"
    assert output[0]["content"] == [{"type": "output_text", "text": "backfilled"}]


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_preserves_existing_terminal_output() -> None:
    """G3 inverse: when upstream already includes terminal `output`,
    the normalizer MUST NOT overwrite it from collected items."""
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.output_item.done","sequence_number":1,"output_index":0,'
                    '"item":{"id":"msg_stream","type":"message","role":"assistant","status":"completed",'
                    '"content":[{"type":"output_text","text":"from-stream-events"}]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":2,'
                    '"response":{"id":"resp_1","object":"response","status":"completed",'
                    '"output":[{"id":"msg_terminal","type":"message","role":"assistant","status":"completed",'
                    '"content":[{"type":"output_text","text":"from-terminal"}]}]}}\n\n'
                ),
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(b) for b in blocks]
    completed = next(p for p in payloads if p and p.get("type") == "response.completed")
    response_obj = completed["response"]
    assert isinstance(response_obj, dict)
    output = response_obj["output"]
    assert isinstance(output, list)
    assert len(output) == 1
    assert output[0]["id"] == "msg_terminal"


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_synthesizes_response_created_on_leading_failure() -> None:
    """G4: when the upstream stream's first standard event is not
    `response.created` (e.g. upstream rejects and emits only
    `response.failed`), the normalizer MUST synthesize a `response.created`
    event from the failed event's envelope so the OpenAI SDK parser can begin.
    """
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.failed","sequence_number":0,'
                    '"response":{"id":"resp_err","object":"response","status":"failed","output":[],'
                    '"error":{"code":"invalid_request_error","message":"bad schema"}}}\n\n'
                ),
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(b) for b in blocks]
    event_types = [p["type"] for p in payloads if p is not None]
    # response.created must be synthesized FIRST, then response.failed forwarded
    assert event_types[:2] == ["response.created", "response.failed"]
    created = payloads[0]
    assert created is not None
    created_response = created["response"]
    assert isinstance(created_response, dict)
    # Synthesized envelope must use in_progress + empty output (the contract
    # values for response.created), not copy "failed" from the source.
    assert created_response["status"] == "in_progress"
    assert created_response["output"] == []
    # But the upstream id is preserved so downstream consumers can correlate.
    assert created_response["id"] == "resp_err"


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_does_not_double_emit_response_created() -> None:
    """G4 inverse: when the upstream stream already starts with
    `response.created`, the normalizer MUST NOT emit a second synthesized one."""
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":1,'
                    '"response":{"id":"resp_1","object":"response","status":"completed",'
                    '"output":[{"id":"msg_1","type":"message","role":"assistant","status":"completed",'
                    '"content":[{"type":"output_text","text":"ok"}]}]}}\n\n'
                ),
            )
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(b) for b in blocks]
    event_types = [p["type"] for p in payloads if p is not None]
    assert event_types.count("response.created") == 1


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_codex_route_preserves_codex_events() -> None:
    """`enforce_openai_sdk_contract=False` (used by /backend-api/codex/*) MUST
    forward `codex.*` vendor events verbatim, MUST NOT backfill terminal
    output, and MUST NOT synthesize a leading `response.created`. The Codex
    CLI consumes the upstream stream natively."""
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                'data: {"type":"codex.rate_limits","plan_type":"pro","rate_limits":{"allowed":true}}\n\n',
                (
                    'data: {"type":"response.failed","sequence_number":0,'
                    '"response":{"id":"resp_err","object":"response","status":"failed","output":[],'
                    '"error":{"code":"x","message":"y"}}}\n\n'
                ),
            ),
            enforce_openai_sdk_contract=False,
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(b) for b in blocks]
    event_types = [p["type"] for p in payloads if p is not None]
    # codex.rate_limits MUST be preserved
    assert "codex.rate_limits" in event_types
    # response.created MUST NOT be synthesized
    assert "response.created" not in event_types
    # Original sequence order preserved
    assert event_types == ["codex.rate_limits", "response.failed"]


@pytest.mark.asyncio
async def test_normalize_public_responses_stream_codex_route_does_not_backfill_output() -> None:
    """`enforce_openai_sdk_contract=False` MUST NOT backfill terminal
    `response.completed.output` from streamed item events. The Codex CLI
    expects upstream's exact shape."""
    blocks = [
        block
        async for block in proxy_api_module._normalize_public_responses_stream(
            _iter_blocks(
                (
                    'data: {"type":"response.created","sequence_number":0,'
                    '"response":{"id":"resp_1","object":"response","status":"in_progress","output":[]}}\n\n'
                ),
                (
                    'data: {"type":"response.output_item.done","sequence_number":1,"output_index":0,'
                    '"item":{"id":"msg_1","type":"message","role":"assistant","status":"completed",'
                    '"content":[{"type":"output_text","text":"hi"}]}}\n\n'
                ),
                (
                    'data: {"type":"response.completed","sequence_number":2,'
                    '"response":{"id":"resp_1","object":"response","status":"completed","output":[]}}\n\n'
                ),
            ),
            enforce_openai_sdk_contract=False,
        )
    ]

    payloads = [proxy_api_module._parse_sse_payload(b) for b in blocks]
    completed = next(p for p in payloads if p and p.get("type") == "response.completed")
    # Output stays empty — Codex CLI handles its own assembly.
    assert completed["response"]["output"] == []
