"""E2E tests: real OpenAI Python SDK against codex-lb /v1/responses via ASGI.

These tests drive the actual ``openai`` package (``client.responses.stream(...)``
and ``client.responses.create(...)``) through the in-process FastAPI app via
``httpx.ASGITransport``. The upstream Codex stream is mocked via
``core_stream_responses`` (same pattern as ``test_proxy_flow.py``) so the tests
cover the entire ``codex-lb`` request/response path — including
``_normalize_public_responses_stream`` — without requiring a real Codex
account.

The goal is to assert that the public ``/v1`` surface is parseable by the
stock OpenAI SDK in all request shapes:

- plain text streaming with an upstream ``codex.rate_limits`` leading event
- tool-call streaming
- structured output (JSON) streaming
- error stream where upstream emits ``response.failed`` without ``response.created``
- terminal ``response.completed`` with empty ``output`` (backfill required)
- non-streaming ``/v1/responses``

See change ``normalize-v1-responses-openai-sdk-stream`` for the audit and design.
"""
from __future__ import annotations

import json

import openai
import pytest
import pytest_asyncio
from httpx import AsyncClient

import app.modules.proxy.service as proxy_module
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Stream payload helpers (build upstream SSE shapes the Codex backend emits)
# ---------------------------------------------------------------------------

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _codex_rate_limits_event() -> str:
    """The vendor event the upstream Codex backend emits before response.created
    (intermittently — throttled per rate-limit window). This event causes the
    OpenAI SDK parser to raise RuntimeError if it leaks onto the public stream.
    """
    return _sse({
        "type": "codex.rate_limits",
        "plan_type": "pro",
        "rate_limits": {"allowed": True, "limit_reached": False},
    })


def _response_created(resp_id: str, seq: int = 0) -> str:
    return _sse({
        "type": "response.created", "sequence_number": seq,
        "response": {"id": resp_id, "object": "response",
                      "status": "in_progress", "output": []},
    })


def _response_completed_empty(resp_id: str, seq: int) -> str:
    """The Codex shape: terminal event with output=[]. Real items come via
    intermediate output_item.done events (which codex-lb must backfill)."""
    return _sse({
        "type": "response.completed", "sequence_number": seq,
        "response": {"id": resp_id, "object": "response",
                      "status": "completed", "output": [],
                      "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}},
    })


def _message_output_block(item_id: str, text: str, output_index: int,
                            start_seq: int) -> list[str]:
    """Emit the full message-item SSE sequence the Codex backend produces
    for a message output: output_item.added -> content_part.added ->
    output_text.delta -> output_text.done -> content_part.done ->
    output_item.done. Returns the list of SSE blocks and the next sequence
    number to use (start_seq + 6).
    """
    blocks = [
        _sse({"type": "response.output_item.added",
               "sequence_number": start_seq, "output_index": output_index,
               "item": {"id": item_id, "type": "message", "role": "assistant",
                          "status": "in_progress", "content": []}}),
        _sse({"type": "response.content_part.added",
               "sequence_number": start_seq + 1,
               "output_index": output_index, "content_index": 0,
               "item_id": item_id,
               "part": {"type": "output_text", "text": ""}}),
        _sse({"type": "response.output_text.delta",
               "sequence_number": start_seq + 2,
               "output_index": output_index, "content_index": 0,
               "item_id": item_id, "delta": text, "logprobs": []}),
        _sse({"type": "response.output_text.done",
               "sequence_number": start_seq + 3,
               "output_index": output_index, "content_index": 0,
               "item_id": item_id, "text": text, "logprobs": []}),
        _sse({"type": "response.content_part.done",
               "sequence_number": start_seq + 4,
               "output_index": output_index, "content_index": 0,
               "item_id": item_id,
               "part": {"type": "output_text", "text": text}}),
        _sse({"type": "response.output_item.done",
               "sequence_number": start_seq + 5, "output_index": output_index,
               "item": {"id": item_id, "type": "message", "role": "assistant",
                          "status": "completed",
                          "content": [{"type": "output_text", "text": text}]}}),
    ]
    return blocks


def _function_call_output_block(call_id: str, name: str, args: str,
                                  output_index: int, start_seq: int) -> list[str]:
    """Emit the full function-call SSE sequence: output_item.added ->
    function_call_arguments.delta -> function_call_arguments.done ->
    output_item.done. (No content_part for function_call items.)"""
    fc_id = f"fc_{call_id}"
    return [
        _sse({"type": "response.output_item.added",
               "sequence_number": start_seq, "output_index": output_index,
               "item": {"id": fc_id, "type": "function_call",
                          "status": "in_progress", "call_id": call_id,
                          "name": name, "arguments": ""}}),
        _sse({"type": "response.function_call_arguments.delta",
               "sequence_number": start_seq + 1,
               "output_index": output_index, "item_id": fc_id, "delta": args}),
        _sse({"type": "response.function_call_arguments.done",
               "sequence_number": start_seq + 2,
               "output_index": output_index, "item_id": fc_id,
               "arguments": args}),
        _sse({"type": "response.output_item.done",
               "sequence_number": start_seq + 3, "output_index": output_index,
               "item": {"id": fc_id, "type": "function_call",
                          "status": "completed", "call_id": call_id,
                          "name": name, "arguments": args}}),
    ]


def _message_output_item_done(item_id: str, text: str, output_index: int, seq: int) -> str:
    """Single-event helper retained for tests that only need the terminal
    item event (e.g. backfill correctness checks)."""
    return _sse({
        "type": "response.output_item.done", "sequence_number": seq,
        "output_index": output_index,
        "item": {"id": item_id, "type": "message", "role": "assistant",
                  "status": "completed",
                  "content": [{"type": "output_text", "text": text}]},
    })


def _function_call_output_item_done(call_id: str, name: str, args: str,
                                      output_index: int, seq: int) -> str:
    return _sse({
        "type": "response.output_item.done", "sequence_number": seq,
        "output_index": output_index,
        "item": {"id": f"fc_{call_id}", "type": "function_call",
                  "status": "completed", "call_id": call_id,
                  "name": name, "arguments": args},
    })


def _response_failed_only(resp_id: str) -> str:
    """Upstream rejection mid-stream — response.failed without response.created.
    The /v1 normalizer must synthesize response.created before forwarding."""
    return _sse({
        "type": "response.failed", "sequence_number": 0,
        "response": {"id": resp_id, "object": "response", "status": "failed",
                      "output": [],
                      "error": {"code": "invalid_request_error",
                                  "message": "bad schema"}},
    })


# ---------------------------------------------------------------------------
# ASGI client fixture + auth setup (reuses e2e conftest fixtures)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gpt-5.5"


def _make_upstream_model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug, display_name=slug, description=f"Test model {slug}",
        context_window=272000, input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False, default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True, minimal_client_version=None,
        priority=0, available_in_plans=frozenset({"plus", "pro"}),
        raw={},
    )


@pytest_asyncio.fixture
async def sdk_client(
    e2e_client: AsyncClient,
    setup_dashboard_password,
    enable_api_key_auth,
    create_api_key,
    import_test_account,
):
    """Build a real openai.AsyncOpenAI client that talks to the in-process
    ASGI app via httpx.ASGITransport. Returns (openai_client, api_key)."""
    await setup_dashboard_password(e2e_client)
    await enable_api_key_auth(e2e_client)
    created = await create_api_key(e2e_client, name="e2e-sdk-key")
    await import_test_account(e2e_client, account_id="acc_e2e_sdk",
                               email="e2e-sdk@example.com")
    # Populate the model registry so the proxy accepts the model name.
    registry = get_model_registry()
    snapshot = {
        "plus": [_make_upstream_model(DEFAULT_MODEL)],
        "pro": [_make_upstream_model(DEFAULT_MODEL)],
    }
    result = registry.update(snapshot)
    if hasattr(result, "__await__"):
        await result
    # Reuse the same ASGITransport that e2e_client built.
    transport = e2e_client._transport  # noqa: SLF001
    client = openai.AsyncOpenAI(
        api_key=created["key"],
        base_url="http://testserver/v1",
        http_client=__import__("httpx").AsyncClient(transport=transport,
                                                    base_url="http://testserver"),
    )
    yield client
    await client.close()


def _patch_upstream_stream(monkeypatch, blocks: list[str]) -> None:
    """Replace ``proxy_module.core_stream_responses`` with a stub that yields
    the given pre-built SSE blocks."""

    async def fake_stream(payload, headers, access_token, account_id,
                          base_url=None, raise_for_status=False):
        for block in blocks:
            yield block

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sdk_responses_stream_plain_text_with_leading_codex_rate_limits(
    sdk_client, monkeypatch,
) -> None:
    """G1 + G3 combined: upstream emits codex.rate_limits before
    response.created, and response.completed carries output=[]. The OpenAI
    SDK stream parser must complete the stream and get_final_response().output
    must contain the message item."""
    resp_id = "resp_plain"
    blocks = [
        _codex_rate_limits_event(),  # MUST be dropped
        _response_created(resp_id, 0),
        *_message_output_block("msg_plain", "hello from stream", 0, 1),
        _response_completed_empty(resp_id, 7),  # MUST be backfilled
    ]
    _patch_upstream_stream(monkeypatch, blocks)

    async with sdk_client.responses.stream(
        model=DEFAULT_MODEL,
        input=[{"role": "user", "content": "hi"}],
    ) as stream:
        events_seen: list[str] = []
        async for event in stream:
            events_seen.append(event.type)
        final = await stream.get_final_response()

    assert "response.created" in events_seen
    # codex.rate_limits MUST NOT reach the SDK
    assert not any(t.startswith("codex.") for t in events_seen)
    assert final.id == resp_id
    assert len(final.output) == 1
    assert final.output[0].type == "message"
    msg = final.output[0]
    assert msg.content[0].text == "hello from stream"


@pytest.mark.asyncio
async def test_sdk_responses_stream_tool_call(sdk_client, monkeypatch) -> None:
    """Streaming tool-call response: function_call output item must survive
    the backfill path and arrive in get_final_response().output."""
    resp_id = "resp_tool"
    blocks = [
        _codex_rate_limits_event(),
        _response_created(resp_id, 0),
        *_function_call_output_block("call_1", "get_weather",
                                       '{"city":"Seoul"}', 0, 1),
        _response_completed_empty(resp_id, 5),
    ]
    _patch_upstream_stream(monkeypatch, blocks)

    async with sdk_client.responses.stream(
        model=DEFAULT_MODEL,
        input=[{"role": "user", "content": "weather?"}],
        tools=[{"type": "function", "name": "get_weather",
                 "description": "get weather",
                 "parameters": {"type": "object",
                                  "properties": {"city": {"type": "string"}},
                                  "required": ["city"]}}],
    ) as stream:
        async for _ in stream:
            pass
        final = await stream.get_final_response()

    assert final.id == resp_id
    assert len(final.output) == 1
    item = final.output[0]
    assert item.type == "function_call"
    assert item.name == "get_weather"
    assert json.loads(item.arguments) == {"city": "Seoul"}


@pytest.mark.asyncio
async def test_sdk_responses_stream_structured_output(
    sdk_client, monkeypatch,
) -> None:
    """Streaming JSON-formatted response. SDK must parse it as a normal
    message output_text item; the proxy is not responsible for JSON validation
    here (that is the model's job), only for SDK-parseable framing."""
    resp_id = "resp_json"
    blocks = [
        _codex_rate_limits_event(),
        _response_created(resp_id, 0),
        *_message_output_block("msg_json", '{"city":"Seoul"}', 0, 1),
        _response_completed_empty(resp_id, 7),
    ]
    _patch_upstream_stream(monkeypatch, blocks)

    async with sdk_client.responses.stream(
        model=DEFAULT_MODEL,
        input=[{"role": "user", "content": "respond as json"}],
        text={"format": {"type": "json_object"}},
    ) as stream:
        async for _ in stream:
            pass
        final = await stream.get_final_response()

    assert len(final.output) == 1
    text = final.output[0].content[0].text
    assert json.loads(text) == {"city": "Seoul"}


@pytest.mark.asyncio
async def test_sdk_responses_stream_upstream_rejection_synthesizes_created(
    sdk_client, monkeypatch,
) -> None:
    """G4: upstream rejects without emitting response.created. The /v1
    normalizer must synthesize response.created so the SDK parser does NOT
    raise RuntimeError; the SDK then observes response.failed cleanly."""
    resp_id = "resp_err"
    _patch_upstream_stream(monkeypatch, [
        _codex_rate_limits_event(),  # dropped
        _response_failed_only(resp_id),  # only standard event
    ])

    events_seen: list[str] = []
    async with sdk_client.responses.stream(
        model=DEFAULT_MODEL,
        input=[{"role": "user", "content": "trigger error"}],
    ) as stream:
        async for event in stream:
            events_seen.append(event.type)
        # get_final_response() raises on failed streams (no response.completed),
        # which is the SDK's normal contract — the test asserts the stream
        # PARSER didn't raise mid-iteration.
    assert "response.created" in events_seen
    assert "response.failed" in events_seen
    assert not any(t.startswith("codex.") for t in events_seen)


@pytest.mark.asyncio
async def test_sdk_responses_non_streaming(sdk_client, monkeypatch) -> None:
    """Non-streaming /v1/responses: SDK must parse the returned JSON into a
    valid Response object with populated output. The non-streaming path already
    has _collect_responses_payload backfill; this test pins the behavior."""
    resp_id = "resp_nonstream"
    _patch_upstream_stream(monkeypatch, [
        _codex_rate_limits_event(),
        _response_created(resp_id, 0),
        _message_output_item_done("msg_n", "non-stream output", 0, 1),
        _response_completed_empty(resp_id, 2),
    ])

    response = await sdk_client.responses.create(
        model=DEFAULT_MODEL,
        input=[{"role": "user", "content": "hi"}],
        stream=False,
    )

    assert response.id == resp_id
    assert response.status == "completed"
    assert len(response.output) == 1
    assert response.output[0].content[0].text == "non-stream output"
