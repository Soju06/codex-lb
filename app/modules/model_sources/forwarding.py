from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from json import JSONDecodeError
from typing import cast

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
class SourceAudioTranscription:
    body: bytes
    content_type: str | None
    usage: SourceUsage | None
    audio_seconds: float | None
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
    try:
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
                        payload=_redact_source_error_payload(
                            _error_payload(data),
                            source,
                            encryptor=encryptor,
                        ),
                        upstream_status_code=response.status,
                    )
                if data is None:
                    raise _invalid_upstream_response_error(response.status)
                return SourceChatCompletion(
                    payload=data,
                    usage=_usage_from_chat_payload(data),
                    upstream_status_code=response.status,
                )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _unreachable_error(exc) from exc


async def stream_chat_completion(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceChatStream:
    usage_holder = SourceUsageHolder()
    usage_parser = SourceStreamUsageParser(usage_holder, response_shape="chat")
    stack, response = await _open_source_stream(source, "/chat/completions", payload, encryptor=encryptor)

    async def body() -> AsyncIterator[bytes]:
        async with stack:
            async for chunk in response.content.iter_chunked(4096):
                usage_parser.feed(chunk)
                yield chunk

    return SourceChatStream(body=body(), usage_holder=usage_holder, upstream_status_code=response.status)


async def forward_responses(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceResponsesCompletion:
    try:
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
                        payload=_redact_source_error_payload(
                            _error_payload(data),
                            source,
                            encryptor=encryptor,
                        ),
                        upstream_status_code=response.status,
                    )
                if data is None:
                    raise _invalid_upstream_response_error(response.status)
                return SourceResponsesCompletion(
                    payload=data,
                    usage=_usage_from_responses_payload(data),
                    upstream_status_code=response.status,
                )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _unreachable_error(exc) from exc


async def forward_audio_transcription(
    source: ModelSource,
    *,
    audio_bytes: bytes,
    filename: str,
    content_type: str | None,
    fields: list[tuple[str, str]],
    encryptor: TokenEncryptor | None = None,
) -> SourceAudioTranscription:
    normalized_filename = filename.strip() if filename else "audio.wav"
    normalized_content_type = content_type.strip() if content_type else "application/octet-stream"
    form = aiohttp.FormData()
    form.add_field(
        "file",
        audio_bytes,
        filename=normalized_filename,
        content_type=normalized_content_type,
    )
    for key, value in fields:
        form.add_field(key, value)
    try:
        async with lease_http_session() as session:
            timeout = aiohttp.ClientTimeout(total=_source_timeout_seconds(source))
            async with session.post(
                _source_url(source, "/audio/transcriptions"),
                headers=_source_headers(source, encryptor=encryptor, accept="*/*", content_type=None),
                data=form,
                timeout=timeout,
            ) as response:
                body = await response.read()
                response_content_type = response.headers.get("Content-Type")
                if response.status >= 400:
                    raise ModelSourceForwardingError(
                        status_code=response.status,
                        payload=_redact_source_error_payload(
                            _error_payload_from_body(body, response_content_type),
                            source,
                            encryptor=encryptor,
                        ),
                        upstream_status_code=response.status,
                    )
                return SourceAudioTranscription(
                    body=body,
                    content_type=response_content_type,
                    usage=_usage_from_audio_body(body, response_content_type),
                    audio_seconds=_audio_seconds_from_body(body, response_content_type),
                    upstream_status_code=response.status,
                )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _unreachable_error(exc) from exc


async def stream_responses(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
) -> SourceResponsesStream:
    usage_holder = SourceUsageHolder()
    usage_parser = SourceStreamUsageParser(usage_holder, response_shape="responses")
    stack, response = await _open_source_stream(source, "/responses", payload, encryptor=encryptor)

    async def body() -> AsyncIterator[bytes]:
        async with stack:
            async for chunk in response.content.iter_chunked(4096):
                usage_parser.feed(chunk)
                yield chunk

    return SourceResponsesStream(body=body(), usage_holder=usage_holder, upstream_status_code=response.status)


async def _open_source_stream(
    source: ModelSource,
    path: str,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None,
) -> tuple[AsyncExitStack, aiohttp.ClientResponse]:
    """Open the upstream request eagerly so errors surface before headers.

    Streaming callers wrap the returned response in a ``StreamingResponse``;
    anything raised after that point arrives after the 200 status line has
    been sent. Opening the request here lets upstream 4xx/5xx and connection
    failures map to a proper OpenAI error response instead of a truncated
    stream. The returned exit stack owns the session lease and response and
    must be closed by the stream body.
    """
    stack = AsyncExitStack()
    try:
        session = await stack.enter_async_context(lease_http_session())
        timeout = aiohttp.ClientTimeout(total=_source_timeout_seconds(source))
        response = await stack.enter_async_context(
            session.post(
                _source_url(source, path),
                headers=_source_headers(source, encryptor=encryptor, stream=True),
                json=payload,
                timeout=timeout,
            )
        )
        if response.status >= 400:
            data = await _response_json(response)
            raise ModelSourceForwardingError(
                status_code=response.status,
                payload=_redact_source_error_payload(
                    _error_payload(data),
                    source,
                    encryptor=encryptor,
                ),
                upstream_status_code=response.status,
            )
        return stack, response
    except (aiohttp.ClientError, TimeoutError) as exc:
        await stack.aclose()
        raise _unreachable_error(exc) from exc
    except BaseException:
        await stack.aclose()
        raise


def _unreachable_error(exc: Exception) -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=502,
        payload={
            "error": {
                "message": f"OpenAI-compatible model source request failed: {exc.__class__.__name__}",
                "type": "upstream_error",
                "code": "model_source_unreachable",
            }
        },
        upstream_status_code=None,
    )


def _source_url(source: ModelSource, path: str) -> str:
    return f"{source.base_url.rstrip('/')}{path}"


def _source_headers(
    source: ModelSource,
    *,
    encryptor: TokenEncryptor | None,
    stream: bool = False,
    accept: str | None = None,
    content_type: str | None = "application/json",
) -> dict[str, str]:
    headers = {
        "Accept": accept or ("text/event-stream" if stream else "application/json"),
    }
    if content_type is not None:
        headers["Content-Type"] = content_type
    if source.api_key_encrypted is not None:
        secret = _source_api_key_secret(source, encryptor=encryptor)
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _source_api_key_secret(source: ModelSource, *, encryptor: TokenEncryptor | None) -> str:
    if source.api_key_encrypted is None:
        return ""
    active_encryptor = encryptor or TokenEncryptor()
    try:
        return active_encryptor.decrypt(source.api_key_encrypted)
    except Exception as exc:
        # A rotated encryption key file or corrupt DB value must surface as a
        # forwarding error (with reservation release at the routes), not a bare 500.
        raise ModelSourceForwardingError(
            status_code=502,
            payload={
                "error": {
                    "message": "OpenAI-compatible model source credentials could not be decrypted",
                    "type": "upstream_error",
                    "code": "model_source_credentials_error",
                }
            },
            upstream_status_code=None,
        ) from exc


def _redact_source_error_payload(
    payload: dict[str, JsonValue],
    source: ModelSource,
    *,
    encryptor: TokenEncryptor | None,
) -> dict[str, JsonValue]:
    if source.api_key_encrypted is None:
        return payload
    secret = _source_api_key_secret(source, encryptor=encryptor)
    if not secret:
        return payload
    redacted = _redact_json_value(payload, secret)
    return cast(dict[str, JsonValue], redacted) if isinstance(redacted, Mapping) else payload


def _redact_json_value(value: JsonValue, secret: str) -> JsonValue:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, list):
        return [_redact_json_value(item, secret) for item in value]
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, JsonValue], value)
        return {key: _redact_json_value(item, secret) for key, item in mapping.items()}
    return value


def _source_timeout_seconds(source: ModelSource) -> float:
    return float(source.timeout_seconds or _DEFAULT_SOURCE_TIMEOUT_SECONDS)


async def _response_json(response: aiohttp.ClientResponse) -> dict[str, JsonValue] | None:
    """Parsed JSON object body, or ``None`` when the body is not valid JSON."""
    try:
        data = await response.json(content_type=None)
    except Exception:
        return None
    return data if isinstance(data, dict) else {"data": data}


def _invalid_upstream_response_error(response_status: int) -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=502,
        payload={
            "error": {
                "message": "OpenAI-compatible model source returned a non-JSON response",
                "type": "upstream_error",
                "code": "invalid_upstream_response",
            }
        },
        upstream_status_code=response_status,
    )


def _error_payload(data: Mapping[str, JsonValue] | None) -> dict[str, JsonValue]:
    error = data.get("error") if data is not None else None
    if is_json_mapping(error):
        return {"error": dict(error)}
    return {
        "error": {
            "message": "OpenAI-compatible model source returned an error",
            "type": "upstream_error",
            "code": "model_source_error",
        }
    }


def _error_payload_from_body(body: bytes, content_type: str | None) -> dict[str, JsonValue]:
    if _is_json_content_type(content_type):
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, JSONDecodeError):
            parsed = None
        if isinstance(parsed, Mapping):
            return _error_payload(parsed)
    upstream_message = _text_error_message(body)
    return {
        "error": {
            "message": upstream_message or "OpenAI-compatible model source returned an error",
            "type": "upstream_error",
            "code": "model_source_error",
        }
    }


def _text_error_message(body: bytes) -> str | None:
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    return text[:1000]


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


def _usage_from_audio_body(body: bytes, content_type: str | None) -> SourceUsage | None:
    if not _is_json_content_type(content_type):
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError):
        return None
    if not is_json_mapping(parsed):
        return None
    usage = parsed.get("usage")
    if not is_json_mapping(usage):
        return None
    return _usage_from_mapping(usage) or _usage_from_responses_mapping(usage) or _usage_from_total_tokens_mapping(usage)


def _audio_seconds_from_body(body: bytes, content_type: str | None) -> float | None:
    """Extract transcribed audio length in seconds from a JSON transcription body.

    Recognizes the top-level ``duration`` field emitted by OpenAI
    ``verbose_json`` and most Whisper-compatible servers, and a
    ``usage.seconds`` / ``usage.duration`` fallback. Non-positive or
    non-numeric values yield ``None`` so duration billing fails closed.
    """
    if not _is_json_content_type(content_type):
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, JSONDecodeError):
        return None
    if not is_json_mapping(parsed):
        return None
    candidate = parsed.get("duration")
    if candidate is None:
        usage = parsed.get("usage")
        if is_json_mapping(usage):
            candidate = usage.get("seconds")
            if candidate is None:
                candidate = usage.get("duration")
    if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
        return None
    seconds = float(candidate)
    return seconds if seconds > 0 else None


def _usage_from_mapping(usage: Mapping[str, JsonValue]) -> SourceUsage | None:
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        return None
    if prompt_tokens < 0 or completion_tokens < 0:
        # Fail closed: negative counts from a misbehaving source would reduce
        # API-key limit counters or record negative cost at settlement.
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
    if input_tokens < 0 or output_tokens < 0:
        # Fail closed: negative counts from a misbehaving source would reduce
        # API-key limit counters or record negative cost at settlement.
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


def _usage_from_total_tokens_mapping(usage: Mapping[str, JsonValue]) -> SourceUsage | None:
    total_tokens = usage.get("total_tokens")
    if not isinstance(total_tokens, int) or total_tokens < 0:
        return None
    return SourceUsage(input_tokens=total_tokens, output_tokens=0)


def _is_json_content_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() in {"application/json", "text/json"}


class SourceStreamUsageParser:
    # A single SSE frame carrying usage is tiny; anything past this cap means
    # the upstream is not producing frame boundaries we recognize, and the
    # parser must not buffer the whole stream in memory.
    _MAX_BUFFER_CHARS = 1_048_576

    def __init__(self, usage_holder: SourceUsageHolder, *, response_shape: str) -> None:
        self._usage_holder = usage_holder
        self._response_shape = response_shape
        self._buffer = ""

    def feed(self, chunk: bytes) -> None:
        # SSE permits CRLF (and bare CR) line endings; normalize so frame
        # detection below only has to handle "\n\n".
        text = chunk.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
        self._buffer += text
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            self._capture_frame(frame)
        if len(self._buffer) > self._MAX_BUFFER_CHARS:
            self._buffer = self._buffer[-self._MAX_BUFFER_CHARS :]

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
