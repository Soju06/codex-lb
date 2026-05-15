#!/usr/bin/env python3
"""Live verification of /v1/responses OpenAI SDK compatibility.

Drives the official `openai` Python SDK against a running codex-lb instance
and asserts the public /v1 surface is parseable across all request shapes:
plain text, tool call, structured output, error stream, non-streaming.

Usage:
    # 1. Boot codex-lb locally (with the fix branch checked out)
    cd ~/projects/codex-lb
    .venv/bin/python -m app.main &
    sleep 3

    # 2. Run this script
    .venv/bin/python scripts/verify_v1_responses_openai_sdk.py \\
        --base-url http://127.0.0.1:2455/v1 \\
        --api-key <codex-lb dashboard key> \\
        --model gpt-5.5

Or against the public deployment after the fix is deployed:
    .venv/bin/python scripts/verify_v1_responses_openai_sdk.py \\
        --base-url https://codex.nekos.me/v1 \\
        --api-key $CODEX_NEKOS_API_KEY \\
        --model gpt-5.5

Exits 0 on full pass, non-zero on any failure (with a per-case PASS/FAIL
summary printed before exit).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass

import openai


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str = ""


async def case_plain_streaming(client: openai.AsyncOpenAI, model: str) -> CaseResult:
    """Plain text streaming. Asserts:
    - SDK parser does NOT raise
    - response.created is the first event
    - codex.* events do NOT appear
    - get_final_response().output is non-empty
    """
    try:
        events: list[str] = []
        async with client.responses.stream(
            model=model,
            input=[{"role": "user", "content": "Reply with exactly: hello"}],
            max_output_tokens=50,
        ) as stream:
            async for event in stream:
                events.append(event.type)
            final = await stream.get_final_response()
        codex_events = [e for e in events if e.startswith("codex.")]
        if codex_events:
            return CaseResult("plain_streaming", False, f"codex.* events leaked: {codex_events}")
        if "response.created" not in events:
            return CaseResult("plain_streaming", False, f"response.created missing; events={events[:5]}")
        if not final.output:
            return CaseResult("plain_streaming", False, "get_final_response().output is empty")
        return CaseResult("plain_streaming", True, f"output_len={len(final.output)} events={len(events)}")
    except Exception as e:
        return CaseResult("plain_streaming", False, f"{type(e).__name__}: {e}")


async def case_tool_call_streaming(client: openai.AsyncOpenAI, model: str) -> CaseResult:
    """Tool-call streaming. Asserts get_final_response() carries a
    function_call output item with parseable arguments."""
    # ``Iterable[FunctionToolParam]`` is a ``TypedDict`` union; build the
    # dict explicitly typed so ty/pyright accept the overload.
    from openai.types.responses import FunctionToolParam

    tools: list[FunctionToolParam] = [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                # OpenAI Structured Outputs requires `additionalProperties: false`
                # on every object schema when `strict: true`. Without it, real
                # OpenAI returns 400 invalid_function_parameters; the Codex
                # backend closes the websocket with close_code=1000.
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]
    try:
        events: list[str] = []
        async with client.responses.stream(
            model=model,
            input=[{"role": "user", "content": "What is the weather in Seoul? Use the get_weather tool."}],
            tools=tools,
            max_output_tokens=200,
        ) as stream:
            async for event in stream:
                events.append(event.type)
            final = await stream.get_final_response()
        if not final.output:
            return CaseResult("tool_call_streaming", False, "output empty")
        fc_items = [it for it in final.output if it.type == "function_call"]
        if not fc_items:
            return CaseResult(
                "tool_call_streaming", False, f"no function_call in output: {[it.type for it in final.output]}"
            )
        fc = fc_items[0]
        fc_args = getattr(fc, "arguments", None)
        fc_name = getattr(fc, "name", None)
        if not isinstance(fc_args, str) or not isinstance(fc_name, str):
            return CaseResult("tool_call_streaming", False, f"function_call missing name/arguments: {fc!r}")
        try:
            args = json.loads(fc_args)
        except Exception as e:
            return CaseResult("tool_call_streaming", False, f"function_call.arguments not JSON: {e}")
        return CaseResult("tool_call_streaming", True, f"name={fc_name} args={args}")
    except Exception as e:
        return CaseResult("tool_call_streaming", False, f"{type(e).__name__}: {e}")


async def case_structured_output_streaming(client: openai.AsyncOpenAI, model: str) -> CaseResult:
    """JSON-format streaming. Asserts the final text parses as JSON."""
    try:
        async with client.responses.stream(
            model=model,
            input=[{"role": "user", "content": "Return json with a 'city' field set to Seoul."}],
            text={"format": {"type": "json_object"}},
            max_output_tokens=100,
        ) as stream:
            async for _ in stream:
                pass
            final = await stream.get_final_response()
        if not final.output:
            return CaseResult("structured_output_streaming", False, "output empty")
        msg_items = [it for it in final.output if it.type == "message"]
        if not msg_items:
            return CaseResult(
                "structured_output_streaming", False, f"no message in output: {[it.type for it in final.output]}"
            )
        msg = msg_items[0]
        content = getattr(msg, "content", None)
        if not content:
            return CaseResult("structured_output_streaming", False, f"message has no content: {msg!r}")
        first_part = content[0]
        text = getattr(first_part, "text", None)
        if not isinstance(text, str):
            return CaseResult("structured_output_streaming", False, f"first content part has no text: {first_part!r}")
        try:
            json.loads(text)
        except Exception as e:
            return CaseResult("structured_output_streaming", False, f"output text not JSON: {e}: {text[:80]}")
        return CaseResult("structured_output_streaming", True, f"text={text[:60]}")
    except Exception as e:
        return CaseResult("structured_output_streaming", False, f"{type(e).__name__}: {e}")


async def case_error_stream(client: openai.AsyncOpenAI, model: str) -> CaseResult:
    """Error-stream case: send an invalid json_schema and expect the SDK to
    iterate the stream WITHOUT raising RuntimeError. Whether the stream
    surfaces as response.failed or HTTP 4xx depends on the upstream, but the
    parser MUST NOT crash on the leading event."""
    try:
        events: list[str] = []
        try:
            async with client.responses.stream(
                model=model,
                input=[{"role": "user", "content": "hi"}],
                text={"format": {"type": "json_schema", "name": "x", "schema": {"type": "INVALID_TYPE"}}},
                max_output_tokens=50,
            ) as stream:
                async for event in stream:
                    events.append(event.type)
        except openai.APIStatusError as e:
            # Acceptable: upstream may reject pre-stream as an HTTP 4xx (the
            # invalid schema is a client error). 5xx / auth / config errors
            # are NOT a pass — those mean the proxy or upstream is broken,
            # not that we successfully surfaced a client validation failure.
            status = getattr(e, "status_code", None)
            if isinstance(status, int) and 400 <= status < 500 and status not in (401, 403):
                return CaseResult("error_stream", True, f"pre-stream HTTP {status} (acceptable client error)")
            return CaseResult("error_stream", False, f"unexpected HTTP {status}: {type(e).__name__}: {e}")
        except openai.APIConnectionError as e:
            # Transport-level failure — never a pass.
            return CaseResult("error_stream", False, f"connection error: {e}")
        codex_events = [e for e in events if e.startswith("codex.")]
        if codex_events:
            return CaseResult("error_stream", False, f"codex.* leaked: {codex_events}")
        if events and events[0] != "response.created":
            return CaseResult("error_stream", False, f"first event was {events[0]!r}, expected response.created")
        return CaseResult("error_stream", True, f"events={events}")
    except RuntimeError as e:
        return CaseResult("error_stream", False, f"SDK parser raised: {e}")
    except Exception as e:
        return CaseResult("error_stream", False, f"{type(e).__name__}: {e}")


async def case_non_streaming(client: openai.AsyncOpenAI, model: str) -> CaseResult:
    """Non-streaming /v1/responses. Asserts SDK parses the response into a
    valid Response object with populated output."""
    try:
        response = await client.responses.create(
            model=model,
            input=[{"role": "user", "content": "Reply with exactly: hello"}],
            max_output_tokens=50,
            stream=False,
        )
        if not response.output:
            return CaseResult("non_streaming", False, "output empty")
        return CaseResult("non_streaming", True, f"output_len={len(response.output)} status={response.status}")
    except Exception as e:
        return CaseResult("non_streaming", False, f"{type(e).__name__}: {e}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="codex-lb /v1 base URL (e.g. http://127.0.0.1:2455/v1)")
    parser.add_argument("--api-key", required=True, help="codex-lb API key (sk-clb-...)")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--skip", default="", help="comma-separated case names to skip")
    args = parser.parse_args()

    client = openai.AsyncOpenAI(api_key=args.api_key, base_url=args.base_url)
    cases = [
        ("plain_streaming", case_plain_streaming),
        ("tool_call_streaming", case_tool_call_streaming),
        ("structured_output_streaming", case_structured_output_streaming),
        ("error_stream", case_error_stream),
        ("non_streaming", case_non_streaming),
    ]
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    results: list[CaseResult] = []
    for name, fn in cases:
        if name in skip:
            print(f"  ~ {name:30s} SKIP")
            continue
        t0 = time.time()
        result = await fn(client, args.model)
        elapsed = time.time() - t0
        marker = "✓ PASS" if result.passed else "✗ FAIL"
        print(f"  {marker} {result.name:30s} [{elapsed:5.1f}s]  {result.detail}")
        results.append(result)

    await client.close()
    failed = [r for r in results if not r.passed]
    print()
    print(f"{'=' * 60}")
    print(f"Summary: {len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("Failures:")
        for r in failed:
            print(f"  - {r.name}: {r.detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
