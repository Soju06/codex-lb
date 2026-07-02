from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

import aiohttp

from app.core.clients.http import lease_http_session
from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.db.models import ModelSource

_DEFAULT_SOURCE_TIMEOUT_SECONDS = 600


class ModelSourceForwardingError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        payload: dict[str, JsonValue],
        upstream_status_code: int | None = None,
    ) -> None:
        super().__init__(str(payload))
        self.status_code = status_code
        self.payload = payload
        self.upstream_status_code = upstream_status_code


@dataclass(frozen=True, slots=True)
class SourceUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class SourceChatCompletion:
    payload: dict[str, JsonValue]
    usage: SourceUsage | None
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class SourceResponsesCompletion:
    payload: dict[str, JsonValue]
    usage: SourceUsage | None
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class SourceChatStream:
    body: AsyncIterator[bytes]
    usage_holder: "SourceUsageHolder"
    upstream_status_code: int


@dataclass(frozen=True, slots=True)
class SourceResponsesStream:
    body: AsyncIterator[bytes]
    usage_holder: "SourceUsageHolder"
    upstream_status_code: int


@dataclass(slots=True)
class SourceUsageHolder:
    usage: SourceUsage | None = None


async def forward_chat_completion(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceChatCompletion:
    async with lease_http_session() as session:
        timeout = aiohttp.ClientTimeout(total=_source_timeout_seconds(source))
        async with session.post(
            _source_url(source, "/chat/completions"),
            headers=_source_headers(source, encryptor=encryptor),
            json=payload,
            timeout=timeout,
        ) as response:
            data = await _response_json(response)
            if response.status >= 400:
                raise ModelSourceForwardingError(
                    status_code=response.status,
                    payload=_error_payload(data),
                    upstream_status_code=response.status,
                )
            return SourceChatCompletion(
                payload=data,
                usage=_usage_from_chat_payload(data),
                upstream_status_code=response.status,
            )


async def stream_chat_completion(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceChatStream:
    usage_holder = SourceUsageHolder()
    usage_parser = SourceStreamUsageParser(usage_holder, response_shape="chat")

    async def body() -> AsyncIterator[bytes]:
        timeout = aiohttp.ClientTimeout(total=_source_timeout_seconds(source))
        async with lease_http_session() as session:
            async with session.post(
                _source_url(source, "/chat/completions"),
                headers=_source_headers(source, encryptor=encryptor),
                json=payload,
                timeout=timeout,
            ) as response:
                if response.status >= 400:
                    data = await _response_json(response)
                    raise ModelSourceForwardingError(
                        status_code=response.status,
                        payload=_error_payload(data),
                        upstream_status_code=response.status,
                    )
                async for chunk in response.content.iter_chunked(4096):
                    usage_parser.feed(chunk)
                    yield chunk

    return SourceChatStream(body=body(), usage_holder=usage_holder, upstream_status_code=200)


async def forward_responses(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceResponsesCompletion:
    async with lease_http_session() as session:
        timeout = aiohttp.ClientTimeout(total=_source_timeout_seconds(source))
        async with session.post(
            _source_url(source, "/responses"),
            headers=_source_headers(source, encryptor=encryptor),
            json=payload,
            timeout=timeout,
        ) as response:
            data = await _response_json(response)
            if response.status >= 400:
                raise ModelSourceForwardingError(
                    status_code=response.status,
                    payload=_error_payload(data),
                    upstream_status_code=response.status,
                )
            return SourceResponsesCompletion(
                payload=data,
                usage=_usage_from_responses_payload(data),
                upstream_status_code=response.status,
            )


async def stream_responses(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceResponsesStream:
    usage_holder = SourceUsageHolder()
    usage_parser = SourceStreamUsageParser(usage_holder, response_shape="responses")

    async def body() -> AsyncIterator[bytes]:
        timeout = aiohttp.ClientTimeout(total=_source_timeout_seconds(source))
        async with lease_http_session() as session:
            async with session.post(
                _source_url(source, "/responses"),
                headers=_source_headers(source, encryptor=encryptor),
                json=payload,
                timeout=timeout,
            ) as response:
                if response.status >= 400:
                    data = await _response_json(response)
                    raise ModelSourceForwardingError(
                        status_code=response.status,
                        payload=_error_payload(data),
                        upstream_status_code=response.status,
                    )
                async for chunk in response.content.iter_chunked(4096):
                    usage_parser.feed(chunk)
                    yield chunk

    return SourceResponsesStream(body=body(), usage_holder=usage_holder, upstream_status_code=200)


def _source_url(source: ModelSource, path: str) -> str:
    return f"{source.base_url.rstrip('/')}{path}"


def _source_headers(source: ModelSource, *, encryptor: TokenEncryptor | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if source.api_key_encrypted is not None:
        active_encryptor = encryptor or TokenEncryptor()
        headers["Authorization"] = f"Bearer {active_encryptor.decrypt(source.api_key_encrypted)}"
    return headers


def _source_timeout_seconds(source: ModelSource) -> float:
    return float(source.timeout_seconds or _DEFAULT_SOURCE_TIMEOUT_SECONDS)


async def _response_json(response: aiohttp.ClientResponse) -> dict[str, JsonValue]:
    try:
        data = await response.json(content_type=None)
    except Exception:
        text = await response.text()
        return {"error": {"message": text[:500], "type": "upstream_error", "code": "invalid_upstream_response"}}
    return data if isinstance(data, dict) else {"data": data}


def _error_payload(data: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    error = data.get("error")
    if is_json_mapping(error):
        return {"error": dict(error)}
    return {
        "error": {
            "message": "OpenAI-compatible model source returned an error",
            "type": "upstream_error",
            "code": "model_source_error",
        }
    }


def _usage_from_chat_payload(payload: Mapping[str, JsonValue]) -> SourceUsage | None:
    usage = payload.get("usage")
    if not is_json_mapping(usage):
        return None
    return _usage_from_mapping(usage)


def _usage_from_responses_payload(payload: Mapping[str, JsonValue]) -> SourceUsage | None:
    usage = payload.get("usage")
    if not is_json_mapping(usage):
        return None
    return _usage_from_responses_mapping(usage)


def _usage_from_mapping(usage: Mapping[str, JsonValue]) -> SourceUsage | None:
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        return None
    cached_tokens = 0
    details = usage.get("prompt_tokens_details")
    if is_json_mapping(details):
        raw_cached = details.get("cached_tokens")
        cached_tokens = raw_cached if isinstance(raw_cached, int) else 0
    return SourceUsage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        cached_input_tokens=max(0, min(cached_tokens, prompt_tokens)),
    )


def _usage_from_responses_mapping(usage: Mapping[str, JsonValue]) -> SourceUsage | None:
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    cached_tokens = 0
    details = usage.get("input_tokens_details")
    if is_json_mapping(details):
        raw_cached = details.get("cached_tokens")
        cached_tokens = raw_cached if isinstance(raw_cached, int) else 0
    return SourceUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=max(0, min(cached_tokens, input_tokens)),
    )


class SourceStreamUsageParser:
    def __init__(self, usage_holder: SourceUsageHolder, *, response_shape: str) -> None:
        self._usage_holder = usage_holder
        self._response_shape = response_shape
        self._buffer = ""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk.decode("utf-8", errors="ignore")
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            self._capture_frame(frame)

    def _capture_frame(self, frame: str) -> None:
        for line in frame.splitlines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            data = stripped.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            try:
                parsed = json.loads(data)
            except ValueError:
                continue
            if not isinstance(parsed, dict):
                continue
            if self._response_shape == "responses":
                usage = _usage_from_responses_event(parsed)
            else:
                usage = _usage_from_chat_payload(parsed)
            if usage is not None:
                self._usage_holder.usage = usage


def _usage_from_responses_event(payload: Mapping[str, JsonValue]) -> SourceUsage | None:
    response = payload.get("response")
    usage = _usage_from_responses_payload(response) if is_json_mapping(response) else None
    if usage is None:
        usage = _usage_from_responses_payload(payload)
    return usage


def _capture_stream_usage(chunk: bytes, usage_holder: SourceUsageHolder) -> None:
    SourceStreamUsageParser(usage_holder, response_shape="chat").feed(chunk)


def _capture_responses_stream_usage(chunk: bytes, usage_holder: SourceUsageHolder) -> None:
    SourceStreamUsageParser(usage_holder, response_shape="responses").feed(chunk)
