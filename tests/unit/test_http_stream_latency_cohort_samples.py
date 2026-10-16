"""HTTP-stream-path regressions for the latency cohort sample seams.

``_stream_once`` yields the upstream terminal frame and only writes its
request-log row in the generator's ``finally``, after the downstream consumer
has drained the frame and the iterator closed. The throughput cohort span must
end when the terminal frame was parsed, not when the row is written, and the
first-token sample must be anchored at the attempt clock, which is re-anchored
after admission right before the upstream send.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import app.core.clients.proxy as proxy_client_module
from app.core.openai.parsing import classify_event_type
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.sse import ParsedSseBlock, parse_sse_data_json
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.streaming.helpers import _HTTPPhaseLatencies
from tests.unit.test_streaming_retry_virtual_time import _make_account, _RequestLogsRecorder, _virtual_service

pytestmark = pytest.mark.unit


def _sse(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


@given(
    boundary=st.sampled_from(["\r", "\n", "\r\n"]),
    space=st.sampled_from(["", " "]),
    event_after_data=st.booleans(),
    representation=st.sampled_from(
        ["header", "payload", "both", "conflicting_created", "conflicting_token", "header_error", "payload_error"]
    ),
    parsed_carrier=st.booleans(),
    ttft_observed=st.booleans(),
    later_frame=st.booleans(),
    observe_created=st.booleans(),
    canonical=st.booleans(),
)
@settings(max_examples=40, deadline=None)
def test_http_phase_observation_ignores_equivalent_keepalives(
    boundary: str,
    space: str,
    event_after_data: bool,
    representation: str,
    parsed_carrier: bool,
    ttft_observed: bool,
    later_frame: bool,
    observe_created: bool,
    canonical: bool,
) -> None:
    payload: dict[str, JsonValue] = {}
    if representation in {"payload", "both", "payload_error"}:
        payload["type"] = "codex.keepalive"
    elif representation == "conflicting_created":
        payload["type"] = "response.created"
    elif representation == "conflicting_token":
        payload.update(type="response.output_text.delta", delta="heartbeat")
    if representation in {"header_error", "payload_error"}:
        payload["error"] = {"code": "server_error", "message": "Upstream failure", "type": "server_error"}
    header = "response.in_progress" if representation in {"payload", "payload_error"} else "codex.keepalive"
    fields = [f"event:{space}{header}", f"data:{space}{json.dumps(payload)}"]
    if event_after_data:
        fields.reverse()
    # A later event field replaces an earlier one, independently of data order.
    if canonical:
        block = f"event: {header}\ndata: {json.dumps(payload)}\n\n"
    else:
        block = boundary.join(["event: ignored", *fields, "", ""])
    if parsed_carrier:
        block = ParsedSseBlock(block, payload)
    block, _ = proxy_client_module._normalize_stream_payload_for_http_block(block)
    normalized_payload = parse_sse_data_json(block)
    phases = _HTTPPhaseLatencies(first_token_ms=125 if ttft_observed else None)

    def unexpected_clock() -> float:
        pytest.fail("Excluded or settled phases must not observe the clock")

    if later_frame:
        assert phases.parse_event(": keepalive\n\n", 1.0, unexpected_clock, {}) == (None, None, False)
    relay_verbatim = (
        canonical and ttft_observed and representation in {"header", "both", "conflicting_created", "conflicting_token"}
    )
    assert phases.parse_event(block, 1.0, unexpected_clock, {}) == (
        (None, header, True) if relay_verbatim else (normalized_payload, classify_event_type(normalized_payload), False)
    )
    assert phases.first_upstream_event_ms is None
    assert phases.response_created_ms is None
    token = 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hello"}\n\n'
    assert phases.parse_event(token, 1.0, lambda: 1.25, {})[2] is ttft_observed
    assert phases.first_upstream_event_ms == 250
    phases.first_token_ms = 250
    if observe_created:
        phases.parse_event(_sse({"type": "response.created"}), 1.0, lambda: 1.5, {})
        phases.parse_event(_sse({"type": "response.created"}), 1.0, unexpected_clock, {})
    assert phases.response_created_ms == (500 if observe_created else None)
    assert phases.parse_event(token, 1.0, unexpected_clock, {}) == (None, "response.output_text.delta", True)


@pytest.mark.asyncio
async def test_http_stream_throughput_sample_stops_at_the_terminal_frame_not_the_downstream_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_logs = _RequestLogsRecorder()
    service, clock, scheduler = _virtual_service(request_logs)
    account = _make_account("acc_http_terminal")

    async def fake_stream(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
        yield _sse({"type": "response.created", "response": {"id": "resp_http_terminal"}})
        clock.advance(2.0)  # first token at t=2 s
        yield _sse({"type": "response.output_text.delta", "delta": "hi"})
        clock.advance(10.0)  # 400 tokens by t=12 s: 40 tok/s of generation
        yield _sse(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_http_terminal",
                    "usage": {"input_tokens": 3_000, "output_tokens": 400},
                },
            }
        )
        # The upstream connection lingers after the terminal frame.
        clock.advance(5.0)

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.6-sol", "instructions": "hi", "input": [], "stream": True, "reasoning": {"effort": "low"}}
    )

    chunks: list[str] = []
    async for chunk in service._stream_once(
        account,
        payload,
        {"session_id": "sid-http-terminal"},
        "req_http_terminal",
        False,
        request_started_at=clock.monotonic(),
        api_key=None,
        api_key_reservation=None,
        settlement=proxy_service._StreamSettlement(),
        suppress_text_done_events=False,
        upstream_stream_transport=None,
        request_transport="http",
    ):
        chunks.append(chunk)
        if '"response.completed"' in chunk:
            # A slow downstream client takes 5 s to drain the terminal frame.
            clock.advance(5.0)
    await scheduler.drain()
    assert await service.drain_persistence_tasks(timeout_seconds=1.0)

    assert len(chunks) == 3
    (row,) = request_logs.calls
    # The persisted row is the wall latency to the generator's close ...
    assert row["latency_ms"] == 22_000
    assert row["latency_first_token_ms"] == 2_000
    # ... while the throughput sample spans first token -> terminal frame only:
    # 400 / 10 s, not 400 / 20 s.
    runtime = service._load_balancer._runtime[account.id]
    ((_, tokens_per_second),) = runtime.tps_samples["gpt-5.6-sol"]
    assert tokens_per_second == pytest.approx(40.0)
    # The attempt clock starts right before the upstream send, so the row's
    # first-token latency is already the account's and is sampled as-is.
    assert [ttft for _, ttft in runtime.ttft_samples] == [2_000]


@pytest.mark.asyncio
async def test_http_stream_without_a_terminal_frame_records_no_throughput_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_logs = _RequestLogsRecorder()
    service, clock, scheduler = _virtual_service(request_logs)
    account = _make_account("acc_http_no_terminal")

    async def fake_stream(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
        yield _sse({"type": "response.created", "response": {"id": "resp_http_no_terminal"}})
        clock.advance(2.0)
        yield _sse({"type": "response.output_text.delta", "delta": "hi"})
        clock.advance(10.0)

    monkeypatch.setattr(proxy_service, "core_stream_responses", fake_stream)
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.6-sol", "instructions": "hi", "input": [], "stream": True}
    )

    chunks = [
        chunk
        async for chunk in service._stream_once(
            account,
            payload,
            {"session_id": "sid-http-no-terminal"},
            "req_http_no_terminal",
            False,
            request_started_at=clock.monotonic(),
            api_key=None,
            api_key_reservation=None,
            settlement=proxy_service._StreamSettlement(),
            suppress_text_done_events=False,
            upstream_stream_transport=None,
            request_transport="http",
        )
    ]
    await scheduler.drain()
    assert await service.drain_persistence_tasks(timeout_seconds=1.0)

    # The upstream ended without a terminal frame: no stamp, an error row, and
    # nothing for either sampler (the row's ``latency_ms`` is the fallback span).
    assert len(chunks) == 2
    (row,) = request_logs.calls
    assert row["status"] == "error"
    assert row["error_code"] == "stream_incomplete"
    runtime = service._load_balancer._runtime.get(account.id)
    assert runtime is None or (not runtime.tps_samples and not runtime.ttft_samples)
