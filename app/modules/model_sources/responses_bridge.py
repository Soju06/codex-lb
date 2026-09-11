"""Shared Responses event encoder for normalized company chat events."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping


class TraeProtocolError(ValueError):
    pass


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    if not is_json_mapping(value):
        raise TraeProtocolError("Expected a JSON object")
    return dict(value)


def _text(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise TraeProtocolError("Expected text")
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    if not is_json_list(value):
        raise TraeProtocolError("Expected a JSON array")
    return value


def _content(value: JsonValue) -> list[JsonValue]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    result: list[JsonValue] = []
    for raw in _list(value):
        part = _mapping(raw)
        kind = part.get("type")
        if kind in ("input_text", "output_text", "text"):
            result.append({"type": "text", "text": _text(part.get("text"))})
        elif kind == "input_image" and isinstance(part.get("image_url"), str):
            result.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
        else:
            raise TraeProtocolError(f"Unsupported TRAE content type: {kind}")
    return result


@dataclass(frozen=True)
class BridgeRequest:
    body: dict[str, JsonValue]
    custom_tools: frozenset[str]
    model: str
    source_id: str
    provider: str = "trae"
    session_id: str = ""


@dataclass
class _ToolCall:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class ResponsesEventDecoder:
    request: BridgeRequest
    response_id: str = field(default_factory=lambda: "resp_" + uuid.uuid4().hex)
    buffer: bytes = b""
    sequence: int = 0
    started: bool = False
    terminal: bool = False
    text: str = ""
    calls: dict[int, _ToolCall] = field(default_factory=dict)
    output: list[JsonValue] = field(default_factory=list)
    usage: dict[str, JsonValue] | None = None
    text_started: bool = False
    extra_info: JsonValue = None
    reasoning_content: str = ""
    phase: str | None = None
    actual_model: str | None = None
    created_at: int = field(default_factory=lambda: int(time.time()))

    def _response(self, status: str) -> dict[str, JsonValue]:
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.request.model,
            "output": self.output,
            "usage": self.usage,
            "error": None,
            "metadata": {self.request.provider + "_model": self.actual_model} if self.actual_model else {},
        }

    def _event(self, kind: str, **values: JsonValue) -> bytes:
        event: dict[str, JsonValue] = {"type": kind, "sequence_number": self.sequence, **values}
        self.sequence += 1
        return ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()

    def fail(self, code: str) -> bytes:
        code = code.replace("trae_", self.request.provider + "_", 1)
        self.terminal = True
        response = self._response("failed")
        response["error"] = {
            "code": code,
            "message": "Company model request failed; inspect source availability or retry later",
        }
        return self._event("response.failed", response=response)

    def feed(self, chunk: bytes) -> bytes:
        if self.terminal:
            return b""
        result = b""
        if not self.started:
            self.started = True
            result += self._event("response.created", response=self._response("in_progress"))
        self.buffer += chunk
        # Bound each upstream record, including malformed streams without separators.
        if len(self.buffer) > 1_048_576:
            return result + self.fail("trae_frame_too_large")
        self.buffer = self.buffer.replace(b"\r\n", b"\n")
        while b"\n\n" in self.buffer and not self.terminal:
            frame, self.buffer = self.buffer.split(b"\n\n", 1)
            event = ""
            data: list[str] = []
            try:
                for line in frame.decode().splitlines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                result += self._frame(event, "\n".join(data))
            except (ValueError, TypeError, KeyError):
                result += self.fail("trae_invalid_event")
        return result

    def finish(self) -> bytes:
        if self.terminal:
            return b""
        result = self.feed(b"\n\n") if self.buffer else b""
        return result if self.terminal else result + self.fail("trae_incomplete_stream")

    def _frame(self, event: str, raw: str) -> bytes:
        if event in ("progress_notice", "ping", ""):
            return b": trae keepalive\n\n"
        data = _mapping(json.loads(raw))
        if event in ("queue_begin", "queue_end", "request_wait_in_queue", "trae_model_queue"):
            return self._event("response.in_progress", response=self._response("in_progress"))
        if event == "error":
            return self.fail("trae_upstream_error")
        if event == "metadata":
            actual = data.get("model")
            if actual is not None:
                self.actual_model = _text(actual)
                if "glm" in self.actual_model.lower():
                    raise TraeProtocolError("GLM is excluded from this deployment")
            return b""
        if event == "timing_cost":
            return b""
        if event == "extra_info":
            self.extra_info = data
            return b""
        if event == "token_usage":
            inp, out = data.get("prompt_tokens"), data.get("completion_tokens")
            if isinstance(inp, int) and isinstance(out, int):
                self.usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
                cached = data.get("cache_read_input_tokens")
                if isinstance(cached, int):
                    self.usage["input_tokens_details"] = {"cached_tokens": cached}
                reasoning = data.get("reasoning_tokens")
                if isinstance(reasoning, int):
                    self.usage["output_tokens_details"] = {"reasoning_tokens": reasoning}
            return b""
        if event == "output":
            if data.get("multimodal_contents"):
                raise TraeProtocolError("TRAE multimodal output requires verified translation")
            if data.get("reasoning_content"):
                self.reasoning_content += _text(data["reasoning_content"])
            phase = data.get("phase")
            if isinstance(phase, str) and phase in ("commentary", "final_answer"):
                if self.phase is not None and self.phase != phase:
                    raise TraeProtocolError("TRAE mixed message phases require separate output items")
                self.phase = phase
            result = b""
            delta = data.get("response", "")
            if isinstance(delta, str) and delta:
                if not self.text_started:
                    self.text_started = True
                    result += self._event(
                        "response.output_item.added",
                        output_index=0,
                        item={
                            "id": self.response_id + "_msg",
                            "type": "message",
                            "role": "assistant",
                            "status": "in_progress",
                            "content": [],
                        },
                    )
                    result += self._event(
                        "response.content_part.added",
                        output_index=0,
                        item_id=self.response_id + "_msg",
                        content_index=0,
                        part={"type": "output_text", "text": "", "annotations": []},
                    )
                self.text += delta
                result += self._event(
                    "response.output_text.delta",
                    output_index=0,
                    item_id=self.response_id + "_msg",
                    content_index=0,
                    delta=delta,
                )
            for raw_call in _list(data.get("tool_calls") or []):
                call = _mapping(raw_call)
                index = call.get("index", 0)
                if not isinstance(index, int) or index < 0 or index > 1024:
                    raise TraeProtocolError("Invalid tool index")
                target = self.calls.setdefault(index, _ToolCall())
                if call.get("id"):
                    target.call_id = _text(call["id"])
                func = _mapping(call.get("function_call", {}))
                if func.get("name"):
                    target.name = _text(func["name"])
                if func.get("arguments"):
                    target.arguments += _text(func["arguments"])
                if len(target.arguments) > 1_048_576:
                    raise TraeProtocolError("Tool arguments too large")
            return result
        if event in ("reasoning_summary", "thinking_summary"):
            return b""
        if event == "done":
            return self._complete(data)
        raise TraeProtocolError("Unsupported TRAE event")

    def _complete(self, data: dict[str, JsonValue]) -> bytes:
        result = b""
        if self.text_started:
            msg: dict[str, JsonValue] = {
                "id": self.response_id + "_msg",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": self.text, "annotations": []}],
            }
            if self.phase:
                msg["phase"] = self.phase
            self.output.append(msg)
            result += self._event(
                "response.output_text.done", output_index=0, item_id=msg["id"], content_index=0, text=self.text
            )
            result += self._event(
                "response.content_part.done",
                output_index=0,
                item_id=msg["id"],
                content_index=0,
                part=_list(msg["content"])[0],
            )
            result += self._event("response.output_item.done", output_index=0, item=msg)
        for index in sorted(self.calls):
            call = self.calls[index]
            if not call.call_id or not call.name:
                raise TraeProtocolError("Incomplete tool call")
            custom = call.name in self.request.custom_tools
            item: dict[str, JsonValue] = {
                "id": "fc_" + uuid.uuid4().hex,
                "type": "custom_tool_call" if custom else "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "status": "completed",
            }
            item["input" if custom else "arguments"] = (
                _text(_mapping(json.loads(call.arguments)).get("input")) if custom else call.arguments
            )
            output_index = len(self.output)
            self.output.append(item)
            result += self._event(
                "response.output_item.added",
                output_index=output_index,
                item={**item, "status": "in_progress", "input" if custom else "arguments": ""},
            )
            result += self._event(
                "response.custom_tool_call_input.delta" if custom else "response.function_call_arguments.delta",
                item_id=item["id"],
                output_index=output_index,
                delta=item["input" if custom else "arguments"],
            )
            result += self._event(
                "response.custom_tool_call_input.done" if custom else "response.function_call_arguments.done",
                item_id=item["id"],
                output_index=output_index,
                **{"input" if custom else "arguments": item["input" if custom else "arguments"]},
            )
            result += self._event("response.output_item.done", output_index=output_index, item=item)
        finish_reason = data.get("finish_reason")
        if finish_reason == "" and str(self.request.body.get("config_name", "")).startswith("gemini-") and self.output:
            finish_reason = "tool_calls" if self.calls else "stop"
        if finish_reason not in ("stop", "tool_calls", "end_turn", "length"):
            return result + self.fail("trae_unknown_finish_reason")
        state = {
            "source_id": self.request.source_id,
            "model": self.request.model,
            "session_id": self.request.session_id or self.request.body["session_id"],
        }
        if self.extra_info is not None:
            state["extra_info"] = self.extra_info
        if self.reasoning_content:
            state["reasoning_content"] = self.reasoning_content
        item: dict[str, JsonValue] = {
            "id": "rs_" + uuid.uuid4().hex,
            "type": "reasoning",
            "summary": [],
            "encrypted_content": self.request.provider + "-v1:" + TokenEncryptor().encrypt(json.dumps(state)).decode(),
        }
        output_index = len(self.output)
        self.output.append(item)
        result += self._event("response.output_item.added", output_index=output_index, item=item)
        result += self._event("response.output_item.done", output_index=output_index, item=item)
        self.terminal = True
        status = "incomplete" if finish_reason == "length" else "completed"
        response = self._response(status)
        if status == "incomplete":
            response["incomplete_details"] = {"reason": "max_output_tokens"}
        return result + self._event("response." + status, response=response)
