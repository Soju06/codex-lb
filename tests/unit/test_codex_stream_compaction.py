from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.core.clients.proxy import ProxyResponseError, compact_responses
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
        self.reached_trailing_wait = True
        await asyncio.sleep(60)


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
    assert exc_info.value.payload["error"]["code"] == "upstream_error"
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
