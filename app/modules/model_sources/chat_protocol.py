"""Responses clients over explicitly configured native Chat Completions models."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from cryptography.fernet import InvalidToken

from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.db.models import ModelSource
from app.modules.model_sources.responses_bridge import (
    BridgeRequest,
    ResponsesEventDecoder,
    TraeProtocolError,
    _content,
    _list,
    _mapping,
    _text,
)

PROVIDER = "codebase_chat"


def uses_chat_backend(source: ModelSource, payload: dict[str, JsonValue]) -> bool:
    if source.kind != "codebase_llm":
        return False
    row = next((m for m in source.models if m.model == payload.get("model") and m.is_enabled), None)
    if row is None:
        return False
    return _mapping(json.loads(row.raw_metadata_json or "{}")).get("native_backend") == "chat"


def prepare_request(source: ModelSource, payload: dict[str, JsonValue]) -> BridgeRequest:
    model = _text(payload.get("model"))
    if payload.get("previous_response_id") or payload.get("conversation"):
        raise TraeProtocolError("Native Chat requires complete input history for continuation")
    if payload.get("text") not in (None, {}) or payload.get("tool_choice") not in (None, "auto"):
        raise TraeProtocolError("Native Chat structured output and forced tool choice are not supported")
    messages: list[JsonValue] = []
    if payload.get("instructions"):
        messages.append({"role": "system", "content": _content(payload["instructions"])})
    inputs: JsonValue = payload.get("input", [])
    if isinstance(inputs, str):
        inputs = [{"role": "user", "content": inputs}]
    names: dict[str, str] = {}
    session_id = str(uuid.uuid4())
    for raw in _list(inputs):
        item = _mapping(raw)
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role")
            if role not in ("user", "assistant", "system", "developer"):
                raise TraeProtocolError("Unsupported native Chat message role")
            messages.append(
                {"role": "system" if role == "developer" else role, "content": _content(item.get("content", ""))}
            )
        elif kind in ("function_call", "custom_tool_call"):
            call_id, name = _text(item.get("call_id")), _text(item.get("name"))
            if call_id in names:
                raise TraeProtocolError("Duplicate native Chat tool call identifier")
            names[call_id] = name
            args = (
                _text(item.get("arguments"))
                if kind == "function_call"
                else json.dumps({"input": _text(item.get("input"))})
            )
            if not messages or _mapping(messages[-1]).get("role") != "assistant":
                messages.append({"role": "assistant", "content": "", "tool_calls": []})
            assistant = _mapping(messages[-1])
            calls = _list(assistant.get("tool_calls", []))
            calls.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": args}})
            assistant["tool_calls"] = calls
            messages[-1] = assistant
        elif kind in ("function_call_output", "custom_tool_call_output"):
            call_id = _text(item.get("call_id"))
            if call_id not in names:
                raise TraeProtocolError("Native Chat tool output requires its preceding call")
            output = item.get("output", "")
            if not isinstance(output, str):
                parts = _content(output)
                output = "\n".join(_text(_mapping(part).get("text")) for part in parts)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
        elif kind == "reasoning":
            encrypted = item.get("encrypted_content")
            if not isinstance(encrypted, str) or not encrypted.startswith(PROVIDER + "-v1:"):
                raise TraeProtocolError("Native Chat requires its own encrypted reasoning state")
            try:
                state = _mapping(json.loads(TokenEncryptor().decrypt(encrypted[len(PROVIDER) + 4 :].encode())))
            except (InvalidToken, ValueError):
                raise TraeProtocolError("Invalid native Chat continuation state") from None
            if state.get("source_id") != source.id or state.get("model") != model:
                raise TraeProtocolError("Native Chat continuation belongs to another source or model")
            session_id = _text(state.get("session_id"))
            if state.get("reasoning_content"):
                index = next(
                    (i for i in range(len(messages) - 1, -1, -1) if _mapping(messages[i]).get("role") == "assistant"),
                    None,
                )
                if index is None:
                    raise TraeProtocolError("Native Chat reasoning requires its assistant message")
                assistant = _mapping(messages[index])
                assistant["reasoning_content"] = _text(state["reasoning_content"])
                messages[index] = assistant
        else:
            raise TraeProtocolError(f"Unsupported native Chat input item: {kind}")
    tools: list[JsonValue] = []
    custom: set[str] = set()
    for raw in _list(payload.get("tools", [])):
        tool = _mapping(raw)
        name = _text(tool.get("name"))
        params: dict[str, JsonValue]
        if tool.get("type") == "function":
            params = _mapping(tool.get("parameters", {}))
        elif tool.get("type") == "custom":
            custom.add(name)
            params = {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            }
        else:
            raise TraeProtocolError("Unsupported native Chat tool type")
        tools.append(
            {
                "type": "function",
                "function": {"name": name, "description": tool.get("description", ""), "parameters": params},
            }
        )
    body: dict[str, JsonValue] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
        body["parallel_tool_calls"] = payload.get("parallel_tool_calls", False)
    for key in ("temperature", "top_p"):
        if payload.get(key) is not None:
            body[key] = payload[key]
    if payload.get("max_output_tokens") is not None:
        body["max_tokens"] = payload["max_output_tokens"]
    reasoning = payload.get("reasoning")
    if is_json_mapping(reasoning) and reasoning.get("effort"):
        body["reasoning_effort"] = reasoning["effort"]
    # Session ID is local continuation state, not an unsupported upstream field.
    request = BridgeRequest(
        body=body,
        custom_tools=frozenset(custom),
        model=model,
        source_id=source.id,
        provider=PROVIDER,
        session_id=session_id,
    )
    return request


@dataclass
class ChatResponsesDecoder(ResponsesEventDecoder):
    finish_reason: str | None = None

    def _frame(self, event: str, raw: str) -> bytes:
        if not raw:
            return b": native chat keepalive\n\n"
        if raw == "[DONE]":
            if self.finish_reason is None:
                return self.fail("codebase_chat_missing_finish_reason")
            return self._complete({"finish_reason": self.finish_reason})
        data = _mapping(json.loads(raw))
        if data.get("error"):
            return self.fail("codebase_chat_upstream_error")
        result = b""
        if data.get("model"):
            result += super()._frame("metadata", json.dumps({"model": data["model"]}))
        usage = data.get("usage")
        if is_json_mapping(usage):
            normalized = dict(usage)
            details = usage.get("prompt_tokens_details")
            if is_json_mapping(details):
                normalized["cache_read_input_tokens"] = details.get("cached_tokens")
            details = usage.get("completion_tokens_details")
            if is_json_mapping(details):
                normalized["reasoning_tokens"] = details.get("reasoning_tokens")
            result += super()._frame("token_usage", json.dumps(normalized))
        for raw_choice in _list(data.get("choices", [])):
            choice = _mapping(raw_choice)
            if choice.get("index", 0) != 0:
                raise TraeProtocolError("Native Chat supports one choice")
            delta = _mapping(choice.get("delta", {}))
            if delta.get("refusal") or delta.get("audio") or delta.get("function_call"):
                raise TraeProtocolError("Unsupported native Chat output")
            calls: list[JsonValue] = []
            for raw_call in _list(delta.get("tool_calls") or []):
                call = _mapping(raw_call)
                calls.append(
                    {"index": call.get("index", 0), "id": call.get("id"), "function_call": call.get("function", {})}
                )
            result += super()._frame(
                "output",
                json.dumps(
                    {
                        "response": delta.get("content"),
                        "reasoning_content": delta.get("reasoning_content"),
                        "tool_calls": calls,
                    }
                ),
            )
            finish = choice.get("finish_reason")
            if finish is not None:
                self.finish_reason = _text(finish)
        return result
