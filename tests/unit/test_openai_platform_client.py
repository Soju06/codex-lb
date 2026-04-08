from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.core.clients.openai_platform as openai_platform_module
from app.core.clients.openai_platform import OpenAIPlatformError, _iter_sse_event_blocks, fetch_models
from app.core.utils.json_guards import is_json_mapping


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        del size
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: object | None = None,
        json_error: Exception | None = None,
        text_body: str | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(chunks)
        self._body = body
        self._json_error = json_error
        self._text_body = text_body
        self.released = False

    async def json(self, content_type=None):
        del content_type
        if self._json_error is not None:
            raise self._json_error
        return self._body

    async def read(self) -> bytes:
        if self._text_body is None:
            return b""
        return self._text_body.encode("utf-8")

    def release(self) -> None:
        self.released = True


class _ResponseContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.get_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    def get(self, url: str, *, headers, timeout):
        self.get_calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _ResponseContext(self._response)

    async def post(self, url: str, *, headers, json, timeout):
        self.post_calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return self._response


@pytest.mark.asyncio
async def test_iter_sse_event_blocks_reassembles_fragmented_events() -> None:
    response = _FakeResponse(
        [
            b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta"}',
            b"\n\n",
            (
                b"event: response.completed\ndata: "
                b'{"type":"response.completed","response":{"id":"resp_1","status":"completed"}}\n'
            ),
            b"\n",
        ]
    )

    events = [event async for event in _iter_sse_event_blocks(response)]

    assert events == [
        'event: response.output_text.delta\ndata: {"type":"response.output_text.delta"}\n\n',
        (
            "event: response.completed\ndata: "
            '{"type":"response.completed","response":{"id":"resp_1","status":"completed"}}\n\n'
        ),
    ]


@pytest.mark.asyncio
async def test_fetch_models_normalizes_non_json_error_body(monkeypatch) -> None:
    response = _FakeResponse(
        [],
        status=502,
        json_error=json.JSONDecodeError("bad json", "<html>upstream outage</html>", 0),
        text_body="<html>upstream outage</html>",
    )
    session = _FakeSession(response)
    monkeypatch.setattr(openai_platform_module, "get_http_client", lambda: SimpleNamespace(session=session))

    with pytest.raises(OpenAIPlatformError) as exc_info:
        await fetch_models(base_url="https://api.openai.com", api_key="sk-test")

    assert exc_info.value.status_code == 502
    error = exc_info.value.payload.get("error")
    assert is_json_mapping(error)
    assert error.get("code") == "platform_http_502"
    assert "upstream outage" in str(error.get("message"))
    assert session.get_calls[0]["url"] == "https://api.openai.com/v1/models"


@pytest.mark.asyncio
async def test_stream_responses_preserves_upstream_request_id(monkeypatch) -> None:
    response = _FakeResponse(
        [
            b'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n',
            (b'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}\n\n'),
        ],
        headers={"x-request-id": "up_req_stream_1"},
    )
    session = _FakeSession(response)
    monkeypatch.setattr(openai_platform_module, "get_http_client", lambda: SimpleNamespace(session=session))

    stream_response = await openai_platform_module.stream_responses(
        base_url="https://api.openai.com",
        payload={"model": "gpt-5.1", "input": "hi"},
        api_key="sk-test",
        organization="org_test",
        project="proj_test",
    )
    events = [event async for event in stream_response.event_stream]

    assert stream_response.upstream_request_id == "up_req_stream_1"
    assert events == [
        'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n',
        'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}\n\n',
    ]
    assert response.released is True
    assert session.post_calls[0]["url"] == "https://api.openai.com/v1/responses"
