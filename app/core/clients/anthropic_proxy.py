from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.core.config.settings import get_settings
from app.core.types import JsonValue


@dataclass(slots=True)
class AnthropicProxyError(Exception):
    status_code: int
    payload: dict[str, JsonValue]

    def __str__(self) -> str:
        return f"Anthropic proxy response error ({self.status_code})"


@dataclass(frozen=True, slots=True)
class _PoolKey:
    model: str
    max_tokens: int | None
    temperature: float | None
    system_prompt: str | None
    cli_path: str | None
    allowed_tools_signature: str | None
    permission_mode: str | None
    cwd: str | None
    max_thinking_tokens: int | None
    mcp_servers_signature: str | None


@dataclass(slots=True)
class _PooledClient:
    client: Any
    broken: bool = False


class _ClientPool:
    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._queue: asyncio.LifoQueue[_PooledClient] = asyncio.LifoQueue(maxsize=max_size)
        self._created = 0
        self._lock = asyncio.Lock()

    async def acquire(self, create_client: Any, *, acquire_timeout_seconds: float) -> _PooledClient:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        should_create = False
        async with self._lock:
            if self._created < self._max_size:
                self._created += 1
                should_create = True

        if should_create:
            try:
                client = await create_client()
                return _PooledClient(client=client)
            except Exception:
                async with self._lock:
                    self._created = max(0, self._created - 1)
                raise

        try:
            return await asyncio.wait_for(self._queue.get(), timeout=acquire_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise AnthropicProxyError(
                503,
                anthropic_error_payload(
                    "api_error",
                    "Timed out waiting for an available Claude SDK session",
                ),
            ) from exc

    async def release(self, pooled: _PooledClient) -> None:
        if pooled.broken:
            await _safe_disconnect(pooled.client)
            async with self._lock:
                self._created = max(0, self._created - 1)
            return

        try:
            self._queue.put_nowait(pooled)
        except asyncio.QueueFull:
            await _safe_disconnect(pooled.client)
            async with self._lock:
                self._created = max(0, self._created - 1)

    async def close(self) -> None:
        drained = 0
        while True:
            try:
                pooled = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            drained += 1
            await _safe_disconnect(pooled.client)
        if drained:
            async with self._lock:
                self._created = max(0, self._created - drained)


class _PoolManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pools: dict[_PoolKey, _ClientPool] = {}

    async def acquire(
        self,
        key: _PoolKey,
        create_client: Any,
        *,
        max_size: int,
        acquire_timeout_seconds: float,
    ) -> _PooledClient:
        async with self._lock:
            pool = self._pools.get(key)
            if pool is None:
                pool = _ClientPool(max_size=max_size)
                self._pools[key] = pool
        return await pool.acquire(create_client, acquire_timeout_seconds=acquire_timeout_seconds)

    async def release(self, key: _PoolKey, pooled: _PooledClient) -> None:
        async with self._lock:
            pool = self._pools.get(key)
        if pool is None:
            await _safe_disconnect(pooled.client)
            return
        await pool.release(pooled)

    async def close_all(self) -> None:
        async with self._lock:
            pools = list(self._pools.values())
            self._pools.clear()
        for pool in pools:
            await pool.close()


_POOL_MANAGER = _PoolManager()


def anthropic_error_payload(error_type: str, message: str) -> dict[str, JsonValue]:
    error_detail: dict[str, JsonValue] = {
        "type": error_type,
        "message": message,
    }
    return {
        "type": "error",
        "error": error_detail,
    }


async def create_message(
    payload: dict[str, JsonValue],
    headers: Mapping[str, str],
    *,
    base_url: str | None = None,
    session: object | None = None,
) -> dict[str, JsonValue]:
    del headers, base_url, session

    message_payload = _build_sdk_query_message(payload)
    session_id = _resolve_session_id(payload) or _ephemeral_session_id()

    try:
        async with _acquire_client(payload) as client:
            await _send_query(client, message_payload, session_id=session_id)
            collected = [message async for message in client.receive_response()]
    except AnthropicProxyError:
        raise
    except Exception as exc:
        raise _map_sdk_error(exc) from exc

    return _build_non_stream_response(collected, requested_model=_extract_request_model(payload))


async def stream_messages(
    payload: dict[str, JsonValue],
    headers: Mapping[str, str],
    *,
    base_url: str | None = None,
    session: object | None = None,
) -> AsyncIterator[str]:
    del headers, base_url, session

    message_payload = _build_sdk_query_message(payload)
    session_id = _resolve_session_id(payload) or _ephemeral_session_id()
    model = _extract_request_model(payload)
    message_id = f"msg_{uuid.uuid4().hex}"
    yielded_start = False
    content_block_index = 0
    emitted_content = False

    try:
        async with _acquire_client(payload) as client:
            await _send_query(client, message_payload, session_id=session_id)

            async for sdk_message in client.receive_response():
                message_type = type(sdk_message).__name__

                if not yielded_start:
                    yielded_start = True
                    yield _to_sse(
                        {
                            "type": "message_start",
                            "message": {
                                "id": message_id,
                                "type": "message",
                                "role": "assistant",
                                "model": model,
                                "content": [],
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": _stream_usage_defaults({}),
                            },
                        }
                    )

                if message_type == "AssistantMessage":
                    for block in _extract_content_blocks(sdk_message):
                        block_type = block.get("type")
                        if block_type == "text":
                            text_value = block.get("text")
                            if not isinstance(text_value, str) or not text_value:
                                continue
                            yield _to_sse(
                                {
                                    "type": "content_block_start",
                                    "index": content_block_index,
                                    "content_block": {"type": "text", "text": ""},
                                }
                            )
                            yield _to_sse(
                                {
                                    "type": "content_block_delta",
                                    "index": content_block_index,
                                    "delta": {"type": "text_delta", "text": text_value},
                                }
                            )
                            yield _to_sse({"type": "content_block_stop", "index": content_block_index})
                            content_block_index += 1
                            emitted_content = True
                        elif block_type in {"tool_use", "tool_result"}:
                            yield _to_sse(
                                {
                                    "type": "content_block_start",
                                    "index": content_block_index,
                                    "content_block": block,
                                }
                            )
                            yield _to_sse({"type": "content_block_stop", "index": content_block_index})
                            content_block_index += 1
                            emitted_content = True

                if message_type == "ResultMessage":
                    if getattr(sdk_message, "is_error", False):
                        error_message = _extract_result_error_message(sdk_message)
                        yield _to_sse(
                            anthropic_error_payload(
                                "api_error",
                                error_message,
                            )
                        )
                        return

                    if not emitted_content:
                        fallback_text = _extract_result_text(sdk_message)
                        if fallback_text:
                            yield _to_sse(
                                {
                                    "type": "content_block_start",
                                    "index": content_block_index,
                                    "content_block": {"type": "text", "text": ""},
                                }
                            )
                            yield _to_sse(
                                {
                                    "type": "content_block_delta",
                                    "index": content_block_index,
                                    "delta": {"type": "text_delta", "text": fallback_text},
                                }
                            )
                            yield _to_sse({"type": "content_block_stop", "index": content_block_index})
                            content_block_index += 1
                            emitted_content = True

                    stop_reason = _extract_stop_reason(sdk_message)
                    usage = _stream_usage_defaults(_extract_usage_fields(sdk_message))
                    yield _to_sse(
                        {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": stop_reason,
                                "stop_sequence": None,
                            },
                            "usage": usage,
                        }
                    )
                    yield _to_sse({"type": "message_stop"})
                    return

        if yielded_start:
            yield _to_sse(
                anthropic_error_payload(
                    "api_error",
                    "Claude SDK stream closed without final result",
                )
            )
    except AnthropicProxyError:
        raise
    except Exception as exc:
        raise _map_sdk_error(exc) from exc


def parse_sse_data_payload(event_block: str) -> dict[str, JsonValue] | None:
    data_lines: list[str] = []
    for raw_line in event_block.splitlines():
        if not raw_line or raw_line.startswith(":"):
            continue
        if not raw_line.startswith("data:"):
            continue
        value = raw_line[5:]
        if value.startswith(" "):
            value = value[1:]
        data_lines.append(value)

    if not data_lines:
        return None
    joined = "\n".join(data_lines).strip()
    if not joined or joined == "[DONE]":
        return None
    try:
        payload = json.loads(joined)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


async def close_anthropic_client_pools() -> None:
    await _POOL_MANAGER.close_all()


def _ephemeral_session_id() -> str:
    return f"codexlb_{uuid.uuid4().hex}"


@asynccontextmanager
async def _acquire_client(payload: dict[str, JsonValue]) -> AsyncIterator[Any]:
    sdk = _require_sdk()
    settings = get_settings()
    if not settings.anthropic_sdk_pool_enabled:
        options = _build_sdk_options(payload)
        client = sdk.ClaudeSDKClient(options)
        await client.connect()
        try:
            yield client
        finally:
            await _safe_disconnect(client)
        return

    key = _pool_key_from_payload(payload)

    async def _create_client() -> Any:
        options = _build_sdk_options(payload)
        client = sdk.ClaudeSDKClient(options)
        await client.connect()
        return client

    pooled = await _POOL_MANAGER.acquire(
        key,
        _create_client,
        max_size=settings.anthropic_sdk_pool_size,
        acquire_timeout_seconds=settings.anthropic_sdk_pool_acquire_timeout_seconds,
    )

    broken = False
    try:
        yield pooled.client
    except Exception:
        broken = True
        raise
    finally:
        if broken:
            pooled.broken = True
        await _POOL_MANAGER.release(key, pooled)


def _pool_key_from_payload(payload: dict[str, JsonValue]) -> _PoolKey:
    settings = get_settings()
    model = _extract_request_model(payload)
    max_tokens = _as_int(payload.get("max_tokens"))
    temperature_raw = payload.get("temperature")
    temperature = float(temperature_raw) if isinstance(temperature_raw, (int, float)) else None
    system_prompt = _extract_system_prompt(payload)
    allowed_tools_signature = _signature_for_json(payload.get("allowed_tools"))
    permission_mode = _normalize_str(payload.get("permission_mode"))
    cwd = _normalize_str(payload.get("cwd"))
    max_thinking_tokens = _as_int(payload.get("max_thinking_tokens"))
    mcp_servers_signature = _signature_for_json(payload.get("mcp_servers"))

    return _PoolKey(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system_prompt=system_prompt,
        cli_path=_normalize_str(settings.anthropic_sdk_cli_path),
        allowed_tools_signature=allowed_tools_signature,
        permission_mode=permission_mode,
        cwd=cwd,
        max_thinking_tokens=max_thinking_tokens,
        mcp_servers_signature=mcp_servers_signature,
    )


def _signature_for_json(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except Exception:
        return None


def _normalize_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


async def _safe_disconnect(client: Any) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass


def _require_sdk() -> Any:
    try:
        sdk = importlib.import_module("claude_agent_sdk")
    except Exception as exc:
        raise AnthropicProxyError(
            503,
            anthropic_error_payload(
                "api_error",
                "claude-agent-sdk is required for Anthropic provider mode",
            ),
        ) from exc

    required_attrs = ("ClaudeSDKClient", "ClaudeAgentOptions")
    for attr in required_attrs:
        if not hasattr(sdk, attr):
            raise AnthropicProxyError(
                503,
                anthropic_error_payload(
                    "api_error",
                    f"claude-agent-sdk missing required attribute: {attr}",
                ),
            )
    return sdk


def _build_sdk_options(payload: dict[str, JsonValue]) -> Any:
    sdk = _require_sdk()
    options = sdk.ClaudeAgentOptions()

    model = _extract_request_model(payload)
    if model and hasattr(options, "model"):
        setattr(options, "model", model)

    max_tokens = payload.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0 and hasattr(options, "max_tokens"):
        setattr(options, "max_tokens", max_tokens)

    temperature = payload.get("temperature")
    if isinstance(temperature, (int, float)) and hasattr(options, "temperature"):
        setattr(options, "temperature", float(temperature))

    system_prompt = _extract_system_prompt(payload)
    if system_prompt and hasattr(options, "system_prompt"):
        setattr(options, "system_prompt", system_prompt)

    session_id = _resolve_session_id(payload)
    if session_id and hasattr(options, "continue_conversation"):
        setattr(options, "continue_conversation", True)

    settings = get_settings()
    cli_path = settings.anthropic_sdk_cli_path
    if cli_path:
        for attr in ("path_to_claude_code_executable", "pathToClaudeCodeExecutable", "cli_path"):
            if hasattr(options, attr):
                setattr(options, attr, cli_path)
                break

    passthrough_option_keys = (
        "allowed_tools",
        "permission_mode",
        "cwd",
        "max_thinking_tokens",
        "mcp_servers",
    )
    for key in passthrough_option_keys:
        value = payload.get(key)
        if value is not None and hasattr(options, key):
            setattr(options, key, value)

    return options


def _build_sdk_query_message(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    prompt = _build_prompt_from_messages(payload)
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": prompt,
        },
    }


def _build_prompt_from_messages(payload: dict[str, JsonValue]) -> str:
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        raise AnthropicProxyError(
            400,
            anthropic_error_payload("invalid_request_error", "messages must be a list"),
        )

    prompt_parts: list[str] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        role = raw_message.get("role")
        if not isinstance(role, str):
            continue
        content = _content_to_text(raw_message.get("content"))
        if not content:
            continue
        prompt_parts.append(f"{role}: {content}")

    if not prompt_parts:
        raise AnthropicProxyError(
            400,
            anthropic_error_payload("invalid_request_error", "messages must include user text content"),
        )

    return "\n\n".join(prompt_parts)


def _content_to_text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if not isinstance(item, Mapping):
                continue
            block_type = item.get("type")
            if block_type == "text":
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value:
                    chunks.append(text_value)
            elif block_type == "tool_result":
                result_content = item.get("content")
                if isinstance(result_content, str) and result_content:
                    chunks.append(result_content)
        return "\n".join(chunks)
    if isinstance(value, Mapping):
        text_value = value.get("text")
        if isinstance(text_value, str):
            return text_value
    return ""


def _extract_system_prompt(payload: dict[str, JsonValue]) -> str | None:
    system_value = payload.get("system")
    if system_value is None:
        return None
    if isinstance(system_value, str):
        stripped = system_value.strip()
        return stripped or None
    if isinstance(system_value, list):
        parts: list[str] = []
        for block in system_value:
            if isinstance(block, str) and block.strip():
                parts.append(block.strip())
                continue
            if isinstance(block, Mapping) and block.get("type") == "text":
                text_value = block.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    parts.append(text_value.strip())
        if parts:
            return "\n\n".join(parts)
    return None


def _extract_request_model(payload: dict[str, JsonValue]) -> str:
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    raise AnthropicProxyError(
        400,
        anthropic_error_payload("invalid_request_error", "model is required"),
    )


def _resolve_session_id(payload: dict[str, JsonValue]) -> str | None:
    direct_session_id = payload.get("session_id")
    if isinstance(direct_session_id, str) and direct_session_id.strip():
        return direct_session_id.strip()

    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        metadata_session_id = metadata.get("session_id")
        if isinstance(metadata_session_id, str) and metadata_session_id.strip():
            return metadata_session_id.strip()

    default_session_id = get_settings().anthropic_sdk_default_session_id
    if default_session_id and default_session_id.strip():
        return default_session_id.strip()
    return None


async def _send_query(client: Any, message_payload: dict[str, JsonValue], *, session_id: str | None) -> None:
    async def message_iter() -> AsyncIterator[dict[str, JsonValue]]:
        yield message_payload

    if session_id:
        await client.query(message_iter(), session_id=session_id)
    else:
        await client.query(message_iter())


def _build_non_stream_response(
    sdk_messages: list[Any],
    *,
    requested_model: str,
) -> dict[str, JsonValue]:
    content_blocks: list[JsonValue] = []
    result_message: Any | None = None

    for sdk_message in sdk_messages:
        message_type = type(sdk_message).__name__
        if message_type == "AssistantMessage":
            content_blocks.extend(_extract_content_blocks(sdk_message))
        if message_type == "ResultMessage":
            result_message = sdk_message

    if result_message is None:
        raise AnthropicProxyError(
            502,
            anthropic_error_payload("api_error", "Claude SDK did not return a result message"),
        )

    if getattr(result_message, "is_error", False):
        raise AnthropicProxyError(
            502,
            anthropic_error_payload("api_error", _extract_result_error_message(result_message)),
        )

    if not content_blocks:
        fallback_text = _extract_result_text(result_message)
        if fallback_text:
            content_blocks.append({"type": "text", "text": fallback_text})

    usage = _extract_usage_fields(result_message)
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content_blocks,
        "stop_reason": _extract_stop_reason(result_message),
        "stop_sequence": None,
        "usage": usage,
    }


def _extract_content_blocks(message: Any) -> list[dict[str, JsonValue]]:
    content_value = getattr(message, "content", None)
    if not isinstance(content_value, list):
        return []

    blocks: list[dict[str, JsonValue]] = []
    for block in content_value:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_value = getattr(block, "text", None)
            if isinstance(text_value, str):
                blocks.append({"type": "text", "text": text_value})
        elif block_type == "tool_use":
            block_id = getattr(block, "id", None)
            name = getattr(block, "name", None)
            tool_input = getattr(block, "input", None)
            if isinstance(block_id, str) and isinstance(name, str) and isinstance(tool_input, dict):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": block_id,
                        "name": name,
                        "input": tool_input,
                    }
                )
        elif block_type == "tool_result":
            tool_use_id = getattr(block, "tool_use_id", None)
            block_content = getattr(block, "content", None)
            is_error = getattr(block, "is_error", None)
            if isinstance(tool_use_id, str):
                tool_result_block: dict[str, JsonValue] = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                }
                if isinstance(block_content, str):
                    tool_result_block["content"] = block_content
                elif isinstance(block_content, list):
                    normalized_content = _normalize_json_list(block_content)
                    if normalized_content is not None:
                        tool_result_block["content"] = normalized_content
                if isinstance(is_error, bool):
                    tool_result_block["is_error"] = is_error
                blocks.append(tool_result_block)
    return blocks


def _normalize_json_list(value: list[Any]) -> list[JsonValue] | None:
    normalized: list[JsonValue] = []
    for item in value:
        if isinstance(item, (str, int, float, bool)) or item is None:
            normalized.append(item)
        elif isinstance(item, Mapping):
            normalized.append(dict(item))
        elif isinstance(item, list):
            nested = _normalize_json_list(item)
            if nested is None:
                return None
            normalized.append(nested)
        else:
            return None
    return normalized


def _extract_usage_fields(result_message: Any) -> dict[str, JsonValue]:
    usage_source = getattr(result_message, "usage", None)
    usage_input = _extract_usage_int(usage_source, "input_tokens")
    usage_output = _extract_usage_int(usage_source, "output_tokens")
    usage_cached = _extract_usage_int(usage_source, "cache_read_input_tokens")
    usage_cache_creation = _extract_usage_int(usage_source, "cache_creation_input_tokens")

    usage: dict[str, JsonValue] = {}
    if usage_input is not None:
        usage["input_tokens"] = usage_input
    if usage_output is not None:
        usage["output_tokens"] = usage_output
    if usage_cached is not None:
        usage["cache_read_input_tokens"] = usage_cached
    if usage_cache_creation is not None:
        usage["cache_creation_input_tokens"] = usage_cache_creation
    return usage


def _stream_usage_defaults(usage: dict[str, JsonValue]) -> dict[str, JsonValue]:
    normalized = dict(usage)
    if "input_tokens" not in normalized:
        normalized["input_tokens"] = 0
    if "output_tokens" not in normalized:
        normalized["output_tokens"] = 0
    if "cache_read_input_tokens" not in normalized:
        normalized["cache_read_input_tokens"] = 0
    if "cache_creation_input_tokens" not in normalized:
        normalized["cache_creation_input_tokens"] = 0
    return normalized


def _extract_usage_int(usage_source: Any, key: str) -> int | None:
    if usage_source is None:
        return None
    if isinstance(usage_source, Mapping):
        value = usage_source.get(key)
    else:
        value = getattr(usage_source, key, None)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _extract_stop_reason(result_message: Any) -> str:
    stop_reason = getattr(result_message, "stop_reason", None)
    if isinstance(stop_reason, str) and stop_reason:
        return stop_reason
    return "end_turn"


def _extract_result_error_message(result_message: Any) -> str:
    result_text = _extract_result_text(result_message)
    if result_text:
        return result_text
    return "Claude SDK returned an error result"


def _extract_result_text(result_message: Any) -> str | None:
    result_value = getattr(result_message, "result", None)
    if isinstance(result_value, str) and result_value.strip():
        return result_value.strip()
    return None


def _to_sse(payload: dict[str, JsonValue]) -> str:
    event_type = payload.get("type")
    event_name = event_type if isinstance(event_type, str) else "message"
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"


def _map_sdk_error(exc: Exception) -> AnthropicProxyError:
    error_name = type(exc).__name__
    message = str(exc).strip() or error_name
    if error_name in {"CLINotFoundError", "CLIConnectionError"}:
        return AnthropicProxyError(503, anthropic_error_payload("api_error", message))
    if error_name in {"ProcessError", "CLIJSONDecodeError"}:
        return AnthropicProxyError(502, anthropic_error_payload("api_error", message))
    if isinstance(exc, TimeoutError):
        return AnthropicProxyError(504, anthropic_error_payload("api_error", "Claude SDK timeout"))
    return AnthropicProxyError(500, anthropic_error_payload("api_error", message))
