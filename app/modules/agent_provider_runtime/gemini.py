from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NotRequired, TypedDict, cast
from urllib.parse import quote

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

GEMINI_DEVELOPER_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiAdapterError(ValueError):
    pass


class OpenAIChatMessageEnvelope(TypedDict):
    role: str
    content: str | None
    tool_calls: NotRequired[list["OpenAIToolCallEnvelope"]]


class OpenAIFunctionEnvelope(TypedDict):
    name: str
    arguments: str


class OpenAIToolCallEnvelope(TypedDict):
    id: str
    type: str
    function: OpenAIFunctionEnvelope
    index: NotRequired[int]
    gemini_thought_signature: NotRequired[str]


class OpenAIChatChoiceEnvelope(TypedDict):
    index: int
    message: OpenAIChatMessageEnvelope
    finish_reason: str | None


class OpenAIChatDeltaEnvelope(TypedDict):
    content: NotRequired[str]
    tool_calls: NotRequired[list[OpenAIToolCallEnvelope]]


class OpenAIChatChunkChoiceEnvelope(TypedDict):
    index: int
    delta: OpenAIChatDeltaEnvelope
    finish_reason: str | None


class OpenAIUsageEnvelope(TypedDict):
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class OpenAIChatCompletionEnvelope(TypedDict):
    id: str
    object: str
    created: int
    model: str
    choices: list[OpenAIChatChoiceEnvelope]
    usage: NotRequired[OpenAIUsageEnvelope]


class OpenAIChatCompletionChunkEnvelope(TypedDict):
    id: str
    object: str
    created: int
    model: str
    choices: list[OpenAIChatChunkChoiceEnvelope]


@dataclass(frozen=True, slots=True)
class GeminiChatRequest:
    model: str
    messages: list[Mapping[str, JsonValue]]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: str | list[str] | None = None
    tools: list[JsonValue] | None = None
    response_format: JsonValue | None = None


def build_generate_content_url(
    model: str,
    *,
    stream: bool = False,
    base_url: str = GEMINI_DEVELOPER_API_BASE_URL,
) -> str:
    action = "streamGenerateContent?alt=sse" if stream else "generateContent"
    return f"{base_url.rstrip('/')}/models/{quote(model, safe='')}:{action}"


def build_generate_content_payload(request: GeminiChatRequest) -> dict[str, JsonValue]:
    if not request.model.strip():
        raise GeminiAdapterError("Gemini model is required")
    system_parts: list[dict[str, JsonValue]] = []
    contents: list[dict[str, JsonValue]] = []
    tool_call_names_by_id: dict[str, str] = {}
    for message in request.messages:
        role = _message_role(message)
        if role in ("system", "developer"):
            system_parts.extend(_message_parts(message))
            continue
        if role == "assistant":
            parts = _assistant_message_parts(message, tool_call_names_by_id)
        elif role == "tool":
            parts = _tool_response_parts(message, tool_call_names_by_id)
        else:
            parts = _message_parts(message)
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": cast(JsonValue, parts)})
    if not contents:
        raise GeminiAdapterError("Gemini request requires at least one user or assistant message")

    payload: dict[str, JsonValue] = {"contents": cast(JsonValue, contents)}
    if system_parts:
        payload["systemInstruction"] = {"parts": cast(JsonValue, system_parts)}

    generation_config = _generation_config(request)
    if generation_config:
        payload["generationConfig"] = cast(JsonValue, generation_config)

    tools = _tools_payload(request.tools or [])
    if tools:
        payload["tools"] = cast(JsonValue, tools)

    return payload


def generate_content_to_chat_completion(
    payload: Mapping[str, JsonValue],
    *,
    model: str,
    created: int | None = None,
) -> OpenAIChatCompletionEnvelope:
    finish_reason = _finish_reason(payload)
    tool_calls = _tool_calls(payload)
    message: OpenAIChatMessageEnvelope = {
        "role": "assistant",
        "content": _response_text(payload) if not tool_calls else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    response: OpenAIChatCompletionEnvelope = {
        "id": _response_id(payload),
        "object": "chat.completion",
        "created": created if created is not None else int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
    }
    usage = _usage_payload(payload)
    if usage:
        response["usage"] = usage
    return response


def generate_content_to_chat_completion_chunk(
    payload: Mapping[str, JsonValue],
    *,
    model: str,
    created: int | None = None,
) -> OpenAIChatCompletionChunkEnvelope:
    tool_calls = _tool_calls(payload, include_index=True)
    delta: OpenAIChatDeltaEnvelope = {}
    if tool_calls:
        delta["tool_calls"] = tool_calls
    else:
        delta["content"] = _response_text(payload)
    return {
        "id": _response_id(payload),
        "object": "chat.completion.chunk",
        "created": created if created is not None else int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": _finish_reason(payload),
            }
        ],
    }


def parse_gemini_sse_data_lines(lines: Iterable[str]) -> list[dict[str, JsonValue]]:
    events: list[dict[str, JsonValue]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        decoded = json.loads(data)
        if not isinstance(decoded, dict):
            raise GeminiAdapterError("Gemini SSE data must decode to an object")
        events.append(cast(dict[str, JsonValue], decoded))
    return events


def chat_completion_chunk_to_sse(chunk: Mapping[str, JsonValue]) -> str:
    return f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"


def _message_role(message: Mapping[str, JsonValue]) -> str:
    role = message.get("role")
    if not isinstance(role, str):
        raise GeminiAdapterError("Message role must be a string")
    if role not in {"system", "developer", "user", "assistant", "tool"}:
        raise GeminiAdapterError(f"Unsupported Gemini message role: {role}")
    return role


def _message_parts(message: Mapping[str, JsonValue]) -> list[dict[str, JsonValue]]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"text": content}]
    if is_json_list(content):
        parts: list[dict[str, JsonValue]] = []
        for part in content:
            parts.extend(_content_part_to_gemini(part))
        if not parts:
            raise GeminiAdapterError("Message content parts cannot be empty")
        return parts
    if content is None:
        return [{"text": ""}]
    raise GeminiAdapterError("Gemini adapter currently supports text content only")


def _assistant_message_parts(
    message: Mapping[str, JsonValue],
    tool_call_names_by_id: dict[str, str],
) -> list[dict[str, JsonValue]]:
    parts: list[dict[str, JsonValue]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        parts.extend(_message_parts(message))
    tool_calls = message.get("tool_calls")
    if is_json_list(tool_calls):
        for tool_call in tool_calls:
            if not is_json_mapping(tool_call):
                continue
            function = tool_call.get("function")
            if not is_json_mapping(function):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            call_id = tool_call.get("id")
            if isinstance(call_id, str) and call_id:
                tool_call_names_by_id[call_id] = name
            function_call: dict[str, JsonValue] = {
                "name": name,
                "args": cast(JsonValue, _json_object_from_arguments(function.get("arguments"))),
            }
            if isinstance(call_id, str) and call_id:
                function_call["id"] = call_id
            part: dict[str, JsonValue] = {"functionCall": cast(JsonValue, function_call)}
            thought_signature = _tool_call_thought_signature(tool_call)
            if thought_signature is not None:
                part["thoughtSignature"] = thought_signature
            parts.append(part)
    if not parts:
        return _message_parts(message)
    return parts


def _tool_response_parts(
    message: Mapping[str, JsonValue],
    tool_call_names_by_id: Mapping[str, str],
) -> list[dict[str, JsonValue]]:
    tool_call_id = message.get("tool_call_id")
    name = message.get("name")
    if not isinstance(name, str) or not name:
        name = tool_call_names_by_id.get(tool_call_id, "") if isinstance(tool_call_id, str) else ""
    if not name:
        name = "tool"
    function_response: dict[str, JsonValue] = {
        "name": name,
        "response": cast(JsonValue, _json_response_from_tool_content(message.get("content"))),
    }
    if isinstance(tool_call_id, str) and tool_call_id:
        function_response["id"] = tool_call_id
    return [{"functionResponse": cast(JsonValue, function_response)}]


def _json_object_from_arguments(value: JsonValue) -> dict[str, JsonValue]:
    if is_json_mapping(value):
        return dict(value)
    if not isinstance(value, str) or not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(decoded) if is_json_mapping(decoded) else {}


def _json_response_from_tool_content(value: JsonValue) -> dict[str, JsonValue]:
    if is_json_mapping(value):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"output": value}
        if is_json_mapping(decoded):
            return dict(decoded)
        return {"output": value}
    return {"output": _tool_content_text(value)}


def _tool_content_text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    if is_json_list(value):
        texts: list[str] = []
        for item in value:
            if isinstance(item, str):
                texts.append(item)
            elif is_json_mapping(item):
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "".join(texts)
    return ""


def _content_part_to_gemini(part: JsonValue) -> list[dict[str, JsonValue]]:
    if isinstance(part, str):
        return [{"text": part}]
    if not is_json_mapping(part):
        raise GeminiAdapterError("Content parts must be strings or objects")
    part_type = part.get("type")
    text = part.get("text")
    if part_type in {"text", "input_text", "output_text"} and isinstance(text, str):
        return [{"text": text}]
    raise GeminiAdapterError("Gemini adapter currently supports text content parts only")


def _generation_config(request: GeminiChatRequest) -> dict[str, JsonValue]:
    config: dict[str, JsonValue] = {}
    if request.temperature is not None:
        config["temperature"] = request.temperature
    if request.top_p is not None:
        config["topP"] = request.top_p
    if request.max_tokens is not None:
        config["maxOutputTokens"] = request.max_tokens
    if isinstance(request.stop, str):
        config["stopSequences"] = [request.stop]
    elif request.stop:
        config["stopSequences"] = cast(JsonValue, request.stop)
    if _is_json_object_response_format(request.response_format):
        config["responseMimeType"] = "application/json"
    return config


def _is_json_object_response_format(value: JsonValue | None) -> bool:
    return is_json_mapping(value) and value.get("type") == "json_object"


def _tools_payload(tools: list[JsonValue]) -> list[dict[str, JsonValue]]:
    declarations: list[dict[str, JsonValue]] = []
    for tool in tools:
        if not is_json_mapping(tool) or tool.get("type") != "function":
            raise GeminiAdapterError("Gemini adapter supports function tools only")
        function = tool.get("function")
        if not is_json_mapping(function):
            raise GeminiAdapterError("Function tool must include a function object")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise GeminiAdapterError("Function tool name is required")
        declaration: dict[str, JsonValue] = {"name": name}
        description = function.get("description")
        if isinstance(description, str):
            declaration["description"] = description
        parameters = function.get("parameters")
        if is_json_mapping(parameters):
            declaration["parameters"] = parameters
        declarations.append(declaration)
    if not declarations:
        return []
    return [{"functionDeclarations": cast(JsonValue, declarations)}]


def _response_text(payload: Mapping[str, JsonValue]) -> str:
    texts: list[str] = []
    for part in _first_candidate_parts(payload):
        if is_json_mapping(part):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts)


def _tool_calls(payload: Mapping[str, JsonValue], *, include_index: bool = False) -> list[OpenAIToolCallEnvelope]:
    calls: list[OpenAIToolCallEnvelope] = []
    for index, part in enumerate(_first_candidate_parts(payload)):
        if not is_json_mapping(part):
            continue
        function_call = part.get("functionCall")
        if not is_json_mapping(function_call):
            continue
        name = function_call.get("name")
        if not isinstance(name, str) or not name:
            continue
        args = function_call.get("args")
        arguments = args if is_json_mapping(args) else {}
        tool_call: OpenAIToolCallEnvelope = {
            "id": f"call_{index}_{name}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, separators=(",", ":")),
            },
        }
        if include_index:
            tool_call["index"] = index
        thought_signature = part.get("thoughtSignature")
        if isinstance(thought_signature, str) and thought_signature:
            tool_call["gemini_thought_signature"] = thought_signature
        calls.append(tool_call)
    return calls


def _tool_call_thought_signature(tool_call: Mapping[str, JsonValue]) -> str | None:
    for key in ("gemini_thought_signature", "thoughtSignature", "thought_signature"):
        value = tool_call.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _finish_reason(payload: Mapping[str, JsonValue]) -> str | None:
    if _tool_calls(payload):
        return "tool_calls"
    candidate = _first_candidate(payload)
    if candidate is None:
        return None
    finish_reason = candidate.get("finishReason")
    if not isinstance(finish_reason, str):
        return None
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
    }.get(finish_reason, finish_reason.lower())


def _response_id(payload: Mapping[str, JsonValue]) -> str:
    response_id = payload.get("responseId")
    if isinstance(response_id, str) and response_id:
        return response_id
    return "gemini-response"


def _usage_payload(payload: Mapping[str, JsonValue]) -> OpenAIUsageEnvelope | None:
    usage = payload.get("usageMetadata")
    if not is_json_mapping(usage):
        return None
    prompt_tokens = _int_or_none(usage.get("promptTokenCount"))
    completion_tokens = _int_or_none(usage.get("candidatesTokenCount"))
    total_tokens = _int_or_none(usage.get("totalTokenCount"))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _first_candidate_parts(payload: Mapping[str, JsonValue]) -> list[JsonValue]:
    candidate = _first_candidate(payload)
    if candidate is None:
        return []
    content = candidate.get("content")
    if not is_json_mapping(content):
        return []
    parts = content.get("parts")
    return parts if is_json_list(parts) else []


def _first_candidate(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    candidates = payload.get("candidates")
    if not is_json_list(candidates) or not candidates:
        return None
    first = candidates[0]
    return first if is_json_mapping(first) else None


def _int_or_none(value: JsonValue) -> int | None:
    return value if isinstance(value, int) else None
