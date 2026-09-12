"""Translate Responses inputs into the TRAE raw-chat request contract."""

from __future__ import annotations

import json
import uuid

from cryptography.fernet import InvalidToken

from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.db.models import ModelSource
from app.modules.model_sources.responses_bridge import (
    BridgeRequest as TraeRequest,
)
from app.modules.model_sources.responses_bridge import (
    ResponsesEventDecoder as TraeResponsesDecoder,
)
from app.modules.model_sources.responses_bridge import (
    TraeProtocolError,
    _content,
    _list,
    _mapping,
    _text,
)

__all__ = ["TraeRequest", "TraeResponsesDecoder", "TraeProtocolError", "prepare_request"]


def prepare_request(source: ModelSource, payload: dict[str, JsonValue]) -> TraeRequest:
    model = _text(payload.get("model"))
    row = next((m for m in source.models if m.model == model and m.is_enabled), None)
    if row is None:
        raise TraeProtocolError("TRAE model is not configured")
    metadata = _mapping(json.loads(row.raw_metadata_json or "{}"))
    config_name = _text(metadata.get("trae_config_name"))
    model_name = _text(metadata.get("trae_model_name"))
    if payload.get("previous_response_id") or payload.get("conversation"):
        raise TraeProtocolError("TRAE requires the complete input history for continuation")
    if payload.get("text") not in (None, {}) or payload.get("tool_choice") not in (None, "auto"):
        raise TraeProtocolError("TRAE structured output and forced tool choice are not supported")
    messages: list[JsonValue] = []
    if payload.get("instructions"):
        messages.append({"role": "system", "content": _content(payload["instructions"])})
    inputs: JsonValue = payload.get("input", [])
    if isinstance(inputs, str):
        inputs = [{"role": "user", "content": inputs}]
    session_id = str(uuid.uuid4())
    names: dict[str, str] = {}
    foreign_compaction_seen = False
    for value in _list(inputs):
        item = _mapping(value)
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role")
            if role not in ("user", "assistant", "system", "developer"):
                raise TraeProtocolError("Unsupported TRAE message role")
            messages.append(
                {"role": "system" if role == "developer" else role, "content": _content(item.get("content", ""))}
            )
        elif kind in ("function_call", "custom_tool_call"):
            call_id, name = _text(item.get("call_id")), _text(item.get("name"))
            names[call_id] = name
            arguments = (
                _text(item.get("arguments"))
                if kind == "function_call"
                else json.dumps({"input": _text(item.get("input"))})
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": [],
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function_call": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            )
        elif kind in ("function_call_output", "custom_tool_call_output"):
            call_id_value = item.get("call_id")
            # Codex desktop can retain app/tool results in an imported or
            # long-lived transcript without the matching Responses tool call.
            # Those records carry ``id`` plus ``name``/``namespace``, but no
            # ``call_id``.  TRAE cannot accept an orphan ``tool`` message, so
            # preserve the result as ordinary context instead of rejecting the
            # entire continuation before it reaches the model.
            if not isinstance(call_id_value, str) or not call_id_value:
                output = item.get("output", "")
                if not isinstance(output, (str, list)):
                    output = json.dumps(output, ensure_ascii=False)
                name = item.get("name")
                namespace = item.get("namespace")
                label_parts = [part for part in (namespace, name) if isinstance(part, str) and part]
                label = ".".join(label_parts) or "tool"
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"[Retained {label} result]"},
                            *_content(output),
                        ],
                    }
                )
                continue
            call_id = call_id_value
            if call_id not in names:
                raise TraeProtocolError("TRAE tool output requires its preceding call in input history")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": names[call_id],
                    "content": _content(item.get("output", "")),
                }
            )
        elif kind == "reasoning":
            encrypted = item.get("encrypted_content")
            if encrypted:
                # Codex keeps provider-private reasoning items in the local
                # transcript when the user switches models.  Their visible
                # assistant message is already present separately, so ignore
                # foreign opaque state instead of making the whole turn
                # unusable on TRAE.
                if not isinstance(encrypted, str) or not encrypted.startswith("trae-v1:"):
                    continue
                try:
                    state = _mapping(json.loads(TokenEncryptor().decrypt(encrypted[8:].encode())))
                except (InvalidToken, ValueError):
                    raise TraeProtocolError("Invalid TRAE continuation state") from None
                if state.get("source_id") != source.id or state.get("model") != model:
                    raise TraeProtocolError("TRAE continuation belongs to another source or model")
                session_id = _text(state.get("session_id"))
                previous = next((m for m in reversed(messages) if _mapping(m).get("role") == "assistant"), None)
                for key in ("extra_info", "reasoning_content"):
                    if state.get(key) is not None:
                        if previous is None:
                            raise TraeProtocolError("TRAE continuation requires its preceding assistant message")
                        if isinstance(previous, dict):
                            previous[key] = state[key]
        elif kind == "compaction":
            # OpenAI compaction payloads are encrypted for that provider and
            # cannot be replayed by TRAE.  Keep later portable messages and
            # tell the model that older context is unavailable.
            foreign_compaction_seen = True
        else:
            raise TraeProtocolError(f"Unsupported TRAE input item: {kind}")
    if foreign_compaction_seen:
        messages.insert(
            0,
            {
                "role": "system",
                "content": _content(
                    "Earlier conversation context was compacted by another provider and cannot be decoded. "
                    "Continue from the visible messages and ask for missing context when needed."
                ),
            },
        )
    tools: list[JsonValue] = []
    custom: set[str] = set()
    for value in _list(payload.get("tools", [])):
        tool = _mapping(value)
        kind = tool.get("type")
        name = _text(tool.get("name"))
        if kind == "function":
            params = _mapping(tool.get("parameters", {}))
        elif kind == "custom":
            custom.add(name)
            params = {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            }
        else:
            raise TraeProtocolError(f"Unsupported TRAE tool type: {kind}")
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": json.dumps(params),
                },
            }
        )
    body: dict[str, JsonValue] = {
        "agent_loop_id": session_id,
        "session_id": session_id,
        "config_name": config_name,
        "model_name": model_name,
        "parallel_tool_calls": False,
        "messages": messages,
        "tools": tools,
    }
    reasoning = payload.get("reasoning")
    if is_json_mapping(reasoning) and reasoning.get("effort"):
        body["reasoning_effort"] = reasoning["effort"]
    if payload.get("max_output_tokens") is not None:
        body["max_tokens"] = payload["max_output_tokens"]
    return TraeRequest(body=body, custom_tools=frozenset(custom), model=model, source_id=source.id)
