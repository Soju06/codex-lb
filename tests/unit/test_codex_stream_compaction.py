from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

import app.core.clients.proxy as proxy_module
from app.core.clients.proxy import ProxyResponseError, compact_responses
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.requests import ResponsesCompactRequest
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


@pytest.fixture
def route() -> ResolvedUpstreamRoute:
    return ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )


class _LegacyCompactResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = (
        b'{"object":"response.compaction","id":"legacy_compact_1","output":'
        b'[{"type":"compaction","id":"cmp_legacy","encrypted_content":"legacy-enc"}]}'
    )

    def json(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "object": "response.compaction",
            "id": "legacy_compact_1",
            "output": [{"type": "compaction", "id": "cmp_legacy", "encrypted_content": "legacy-enc"}],
        }


class _UnsupportedCompactResponse:
    status_code = 404
    headers = {"content-type": "application/json"}
    content = b'{"error":{"type":"invalid_request_error","code":"not_found","message":"Not Found"}}'

    def json(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "error": {
                "type": "invalid_request_error",
                "code": "not_found",
                "message": "Not Found",
            }
        }


class _CompactionStreamContent:
    async def iter_chunked(self, size: int):
        del size
        yield (
            b'data: {"type":"response.output_item.done","output_index":0,'
            b'"item":{"id":"cmp_stream","type":"compaction","status":"completed",'
            b'"encrypted_content":"stream-enc"}}\n\n'
        )
        yield (
            b'data: {"type":"response.completed","response":{"id":"stream_compact_1",'
            b'"object":"response","status":"completed","usage":{"input_tokens":7,'
            b'"output_tokens":2,"total_tokens":9}}}\n\n'
        )


class _CompactionStreamContentWithTrailingHang:
    def __init__(self) -> None:
        self.reached_trailing_wait = False
        self.closed = False

    async def iter_chunked(self, size: int):
        del size
        try:
            yield (
                b'data: {"type":"response.output_item.done","output_index":0,'
                b'"item":{"id":"cmp_stream","type":"compaction","status":"completed",'
                b'"encrypted_content":"stream-enc"}}\n\n'
            )
            yield (
                b'data: {"type":"response.completed","response":{"id":"stream_compact_1",'
                b'"object":"response","status":"completed","usage":{"input_tokens":7,'
                b'"output_tokens":2,"total_tokens":9}}}\n\n'
            )
            self.reached_trailing_wait = True
            await asyncio.sleep(60)
        finally:
            self.closed = True


class _CompactionIncompleteStreamContent:
    async def iter_chunked(self, size: int):
        del size
        yield (
            b'data: {"type":"response.output_item.done","output_index":0,'
            b'"item":{"id":"cmp_incomplete","type":"compaction","status":"completed",'
            b'"encrypted_content":"partial-enc"}}\n\n'
        )
        yield (
            b'data: {"type":"response.incomplete","response":{"id":"stream_compact_incomplete",'
            b'"object":"response","status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}}\n\n'
        )


class _CompactionStreamContentWithServiceTier:
    async def iter_chunked(self, size: int):
        del size
        yield (
            b'data: {"type":"response.output_item.done","output_index":0,'
            b'"item":{"id":"cmp_stream","type":"compaction","status":"completed",'
            b'"encrypted_content":"stream-enc"}}\n\n'
        )
        yield (
            b'data: {"type":"response.completed","response":{"id":"stream_compact_1",'
            b'"object":"response","status":"completed","service_tier":"default",'
            b'"usage":{"input_tokens":7,"output_tokens":2,"total_tokens":9}}}\n\n'
        )


class _CompactionStreamErrorContent:
    async def iter_chunked(self, size: int):
        del size
        yield (
            b'data: {"type":"error","status":401,"error":{"message":"expired token",'
            b'"type":"invalid_request_error","code":"token_expired","param":"input",'
            b'"plan_type":"pro","resets_at":1700003600,"resets_in_seconds":45}}\n\n'
        )


class _CompactionStreamResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, content: object | None = None) -> None:
        self.content = content or _CompactionStreamContent()


class _SequenceCodexClient:
    def __init__(self, responses: list[object]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = iter(responses)

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        return next(self.responses)


async def _wait_for_stream_close(content: _CompactionStreamContentWithTrailingHang) -> None:
    while not content.closed:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_compact_responses_uses_responses_stream_compaction_v2(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _SequenceCodexClient([_CompactionStreamResponse()])
    payload = ResponsesCompactRequest(
        model="gpt-5.6",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        use_responses_stream_compaction=True,
    )

    assert response.object == "response.compaction"
    assert response.id == "stream_compact_1"
    assert response.usage is not None
    assert response.usage.input_tokens == 7
    assert response.model_extra is not None
    assert response.model_extra["output"] == [
        {
            "id": "cmp_stream",
            "type": "compaction",
            "status": "completed",
            "encrypted_content": "stream-enc",
        }
    ]
    assert client.calls[0]["url"].endswith("/backend-api/codex/responses")
    assert client.calls[0]["buffer_response"] is False
    assert client.calls[0]["json"]["stream"] is True
    assert client.calls[0]["json"]["input"][-1] == {"type": "compaction_trigger"}


@pytest.mark.asyncio
async def test_compact_responses_stream_compaction_stops_at_completed(
    route: ResolvedUpstreamRoute,
) -> None:
    content = _CompactionStreamContentWithTrailingHang()
    client = _SequenceCodexClient([_CompactionStreamResponse(content)])
    payload = ResponsesCompactRequest(
        model="gpt-5.6",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        use_responses_stream_compaction=True,
    )

    assert response.object == "response.compaction"
    assert response.id == "stream_compact_1"
    assert content.reached_trailing_wait is False
    await asyncio.wait_for(_wait_for_stream_close(content), timeout=0.1)


@pytest.mark.asyncio
async def test_compact_responses_stream_compaction_surfaces_incomplete_terminal(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _SequenceCodexClient([_CompactionStreamResponse(_CompactionIncompleteStreamContent())])
    payload = ResponsesCompactRequest(
        model="gpt-5.6",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, client),
            use_responses_stream_compaction=True,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.failure_phase == "stream"
    assert exc_info.value.failure_detail == "response_incomplete"
    assert exc_info.value.payload["error"]["code"] == "max_output_tokens"
    assert "ended incomplete" in exc_info.value.payload["error"]["message"]
    assert "max_output_tokens" in exc_info.value.payload["error"]["message"]


@pytest.mark.asyncio
async def test_compact_responses_falls_back_to_legacy_endpoint_when_stream_is_unsupported(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _SequenceCodexClient([_UnsupportedCompactResponse(), _LegacyCompactResponse()])
    payload = ResponsesCompactRequest(
        model="gpt-5.2",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        use_responses_stream_compaction=True,
    )

    assert response.object == "response.compaction"
    assert response.id == "legacy_compact_1"
    assert len(client.calls) == 2
    assert client.calls[0]["json"]["input"][-1] == {"type": "compaction_trigger"}
    assert client.calls[0]["json"]["stream"] is True


@pytest.mark.asyncio
async def test_compact_responses_stream_compaction_preserves_service_tier(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _SequenceCodexClient([_CompactionStreamResponse(_CompactionStreamContentWithServiceTier())])
    payload = ResponsesCompactRequest(
        model="gpt-5.6",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        use_responses_stream_compaction=True,
    )

    assert response.service_tier == "default"


@pytest.mark.asyncio
async def test_compact_responses_stream_compaction_preserves_stream_error_metadata_and_status(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _SequenceCodexClient([_CompactionStreamResponse(_CompactionStreamErrorContent())])
    payload = ResponsesCompactRequest(
        model="gpt-5.6",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, client),
            use_responses_stream_compaction=True,
        )

    error = exc_info.value.payload["error"]
    assert exc_info.value.status_code == 401
    assert error["code"] == "token_expired"
    assert error["param"] == "input"
    assert error["plan_type"] == "pro"
    assert error["resets_at"] == 1700003600
    assert error["resets_in_seconds"] == 45


@pytest.mark.asyncio
async def test_compact_responses_stream_compaction_validates_wire_budget_after_trigger(
    route: ResolvedUpstreamRoute,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SequenceCodexClient([_CompactionStreamResponse()])
    payload = ResponsesCompactRequest(
        model="gpt-5.6",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )

    def fail_only_after_trigger(payload_dict: dict[str, Any]) -> None:
        input_value = payload_dict.get("input")
        if (
            isinstance(input_value, list)
            and input_value
            and isinstance(input_value[-1], dict)
            and input_value[-1].get("type") == "compaction_trigger"
        ):
            raise ClientPayloadError(
                "trigger pushes payload over budget",
                code="responses_compact_input_too_large",
                param="input",
            )

    monkeypatch.setattr(proxy_module, "validate_compact_input_wire_budget", fail_only_after_trigger)

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, client),
            use_responses_stream_compaction=True,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.payload["error"]["code"] == "responses_compact_input_too_large"
    assert exc_info.value.payload["error"]["param"] == "input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_compact_responses_stream_compaction_rejects_conversation_and_previous_response_id(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _SequenceCodexClient([_CompactionStreamResponse()])
    payload = ResponsesCompactRequest.model_validate(
        {
            "model": "gpt-5.6",
            "instructions": "Summarize.",
            "input": [{"role": "user", "content": "hello"}],
            "conversation": "conv_123",
            "previous_response_id": "resp_123",
        }
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, client),
            use_responses_stream_compaction=True,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.payload["error"]["code"] == "invalid_request_error"
    assert exc_info.value.payload["error"]["param"] == "previous_response_id"
    assert client.calls == []


@pytest.mark.asyncio
async def test_compact_responses_stream_success_does_not_log_legacy_compact_request(
    route: ResolvedUpstreamRoute,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SequenceCodexClient([_CompactionStreamResponse()])
    payload = ResponsesCompactRequest(
        model="gpt-5.6",
        instructions="Summarize.",
        input=[{"role": "user", "content": "hello"}],
    )
    legacy_start_calls: list[dict[str, Any]] = []
    legacy_archive_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        proxy_module,
        "_maybe_log_upstream_request_start",
        lambda **kwargs: legacy_start_calls.append(kwargs),
    )
    monkeypatch.setattr(proxy_module, "archive_json", lambda **kwargs: legacy_archive_calls.append(kwargs))

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        use_responses_stream_compaction=True,
    )

    assert response.object == "response.compaction"
    assert all(call["kind"] != "responses_compact" for call in legacy_start_calls)
    assert all(call["kind"] != "compact" for call in legacy_archive_calls)
