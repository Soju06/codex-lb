from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.tool_call_safety import is_downstream_side_effect_tool_call_item
from app.core.types import JsonObject, JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

type MutableJsonObject = dict[str, JsonValue]

_RESPONSES_INCLUDE_ALLOWLIST = {
    "code_interpreter_call.outputs",
    "computer_call_output.output.image_url",
    "file_search_call.results",
    "message.input_image.image_url",
    "message.output_text.logprobs",
    "reasoning.encrypted_content",
    "web_search_call.action.sources",
}

UNSUPPORTED_TOOL_TYPES = {
    "file_search",
    "code_interpreter",
    "computer_use",
    "computer_use_preview",
    "image_generation",
}

_TOOL_TYPE_ALIASES = {
    "web_search_preview": "web_search",
}

_INTERLEAVED_REASONING_KEYS = frozenset({"reasoning_content", "reasoning_details", "tool_calls", "function_call"})
_INTERLEAVED_REASONING_PART_TYPES = frozenset({"reasoning", "reasoning_content", "reasoning_details"})
_ASSISTANT_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_TOOL_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text", "refusal"})
_COMPACT_STATE_TOOL_NAMES = frozenset({"create_goal", "get_goal", "update_goal", "update_plan"})
_COMPACT_TOOL_CALL_ITEM_TYPES = frozenset({"function_call", "custom_tool_call", "apply_patch_call"})
_COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES = frozenset(
    {"function_call_output", "custom_tool_call_output", "apply_patch_call_output"}
)
_GOAL_CONTINUATION_CONTEXT_PREFIX = '<codex_internal_context source="goal">'
_PLAN_MODE_CONTEXT_PREFIX = "<collaboration_mode># Plan Mode"


def _json_mapping_or_none(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if not is_json_mapping(value):
        return None
    return value


def _json_parts(value: JsonValue) -> list[JsonValue]:
    if is_json_list(value):
        return value
    return [value]


def normalize_tool_type(tool_type: str) -> str:
    return _TOOL_TYPE_ALIASES.get(tool_type, tool_type)


def normalize_tool_choice(choice: JsonValue | None) -> JsonValue | None:
    if not is_json_mapping(choice):
        return choice
    choice_mapping = choice
    tool_type = choice_mapping.get("type")
    if isinstance(tool_type, str):
        normalized_type = normalize_tool_type(tool_type)
        if normalized_type != tool_type:
            updated = dict(choice_mapping)
            updated["type"] = normalized_type
            return updated
    return choice


def validate_tool_types(tools: list[JsonValue], *, allow_builtin_tools: bool = False) -> list[JsonValue]:
    normalized_tools: list[JsonValue] = []
    for tool in tools:
        if not is_json_mapping(tool):
            normalized_tools.append(tool)
            continue
        tool_mapping = tool
        tool_type = tool_mapping.get("type")
        if isinstance(tool_type, str):
            normalized_type = normalize_tool_type(tool_type)
            if normalized_type != tool_type:
                tool = dict(tool_mapping)
                tool["type"] = normalized_type
                tool_type = normalized_type
            if not allow_builtin_tools and tool_type in UNSUPPORTED_TOOL_TYPES:
                raise ValueError(f"Unsupported tool type: {tool_type}")
        normalized_tools.append(tool)
    return normalized_tools


def _has_input_file_id(input_items: list[JsonValue]) -> bool:
    for item in input_items:
        if not is_json_mapping(item):
            continue
        item_mapping = item
        if _is_input_file_with_id(item_mapping):
            return True
        content = item_mapping.get("content")
        if is_json_list(content):
            parts = content
        elif is_json_mapping(content):
            parts = [content]
        else:
            parts = []
        for part in parts:
            if not is_json_mapping(part):
                continue
            if _is_input_file_with_id(part):
                return True
    return False


def _is_input_file_with_id(item: Mapping[str, JsonValue]) -> bool:
    if item.get("type") != "input_file":
        return False
    file_id = item.get("file_id")
    return isinstance(file_id, str) and bool(file_id)


@dataclass(frozen=True, slots=True)
class InputImageFileReference:
    item_index: int
    content_index: int | None
    file_id: str


def _input_image_file_reference(item: Mapping[str, JsonValue]) -> str | None:
    if item.get("type") != "input_image":
        return None
    file_id = item.get("file_id")
    if isinstance(file_id, str) and file_id:
        return file_id
    image_url = item.get("image_url")
    if not isinstance(image_url, str) or not image_url.startswith("sediment://"):
        return None
    resolved = image_url.removeprefix("sediment://").strip()
    return resolved or None


def extract_input_file_ids(input_value: JsonValue) -> set[str]:
    """Return all ``file_id`` strings referenced by ``input_file`` / ``input_image`` items.

    Walks both top-level items and nested role-message ``content`` parts,
    matching the shapes accepted by ``ResponsesRequest.input`` /
    ``ResponsesCompactRequest.input``. Returns an empty set when the
    input is a plain string or has no ``input_file`` parts. Used by the
    ``/responses`` flow to look up account pins recorded by
    ``POST /backend-api/files`` so the response request lands on the
    upstream account that registered the file (the upstream contract is
    account-scoped via ``chatgpt-account-id``).
    """
    if not is_json_list(input_value):
        return set()
    file_ids: set[str] = set()
    for item in input_value:
        if not is_json_mapping(item):
            continue
        item_mapping = item
        if _is_input_file_with_id(item_mapping):
            file_id = item_mapping.get("file_id")
            if isinstance(file_id, str) and file_id:
                file_ids.add(file_id)
        image_file_id = _input_image_file_reference(item_mapping)
        if image_file_id is not None:
            file_ids.add(image_file_id)
        content = item_mapping.get("content")
        if is_json_list(content):
            parts: list[JsonValue] = content
        elif is_json_mapping(content):
            parts = [content]
        else:
            parts = []
        for part in parts:
            if not is_json_mapping(part):
                continue
            if _is_input_file_with_id(part):
                file_id = part.get("file_id")
                if isinstance(file_id, str) and file_id:
                    file_ids.add(file_id)
            image_file_id = _input_image_file_reference(part)
            if image_file_id is not None:
                file_ids.add(image_file_id)
    return file_ids


def _append_input_image_file_references(
    references: list[InputImageFileReference],
    value: JsonValue,
    *,
    item_index: int,
    content_index: int | None,
) -> None:
    if is_json_mapping(value):
        file_id = _input_image_file_reference(value)
        if file_id is not None:
            references.append(
                InputImageFileReference(
                    item_index=item_index,
                    content_index=content_index,
                    file_id=file_id,
                )
            )
        for child in value.values():
            _append_input_image_file_references(
                references,
                child,
                item_index=item_index,
                content_index=content_index,
            )
        return
    if is_json_list(value):
        for child in value:
            _append_input_image_file_references(
                references,
                child,
                item_index=item_index,
                content_index=content_index,
            )


def extract_input_image_file_references(input_value: JsonValue) -> list[InputImageFileReference]:
    if not is_json_list(input_value):
        return []
    references: list[InputImageFileReference] = []
    for item_index, item in enumerate(input_value):
        if not is_json_mapping(item):
            continue
        item_mapping = item
        top_level_file_id = _input_image_file_reference(item_mapping)
        if top_level_file_id is not None:
            references.append(
                InputImageFileReference(
                    item_index=item_index,
                    content_index=None,
                    file_id=top_level_file_id,
                )
            )
        content = item_mapping.get("content")
        if is_json_list(content):
            parts: list[JsonValue] = content
        elif is_json_mapping(content):
            parts = [content]
        else:
            parts = []
        for content_index, part in enumerate(parts):
            _append_input_image_file_references(
                references,
                part,
                item_index=item_index,
                content_index=content_index,
            )
        output = item_mapping.get("output")
        _append_input_image_file_references(
            references,
            output,
            item_index=item_index,
            content_index=None,
        )
    return references


def _is_preserved_non_message_directive(item: Mapping[str, JsonValue]) -> bool:
    # Shared preservation rule: any system/developer-role input item whose type
    # is present and not "message" must reach upstream byte-identical. It gates
    # instruction hoisting, input sanitization, and compact trim anchoring.
    if item.get("role") not in ("system", "developer"):
        return False
    item_type = item.get("type")
    return item_type is not None and item_type != "message"


def _sanitize_input_items(input_items: list[JsonValue]) -> list[JsonValue]:
    sanitized_input: list[JsonValue] = []
    for item in input_items:
        item_mapping = _json_mapping_or_none(item)
        if item_mapping is not None and _is_preserved_non_message_directive(item_mapping):
            # Preserved directives are forwarded unchanged; the interleaved
            # reasoning sanitizer targets assistant-message echo fields and
            # must not strip keys such as tool_calls or reasoning_content
            # from a typed directive.
            sanitized_input.append(item)
            continue
        sanitized_item = _sanitize_interleaved_reasoning_input_item(item)
        if sanitized_item is None:
            continue
        sanitized_input.append(_normalize_role_input_item(sanitized_item))
    return sanitized_input


def _normalize_responses_input_instructions(data: JsonValue) -> JsonValue:
    if not is_json_mapping(data):
        return data
    input_value = data.get("input")
    if not is_json_list(input_value):
        return data
    # Codex deliberately places the Lite tool bundle and base instructions in
    # the input prefix. Keep that wire shape intact instead of lifting its
    # developer message into the top-level ``instructions`` field.
    if responses_input_uses_lite_tools(input_value):
        return data

    instruction_parts: list[str] = []
    input_items: list[JsonValue] = []
    changed = False
    for item in input_value:
        item_mapping = _json_mapping_or_none(item)
        if item_mapping is None:
            input_items.append(item)
            continue
        # Only hoist actual message items (type omitted or "message"). Non-message
        # typed system/developer items (e.g. the Codex responses-lite
        # {"type": "additional_tools", "role": "developer", "tools": [...]} bundle, or
        # any future typed item) carry no instruction content; hoisting used to drop
        # them entirely, so pass them through untouched regardless of role.
        if _is_preserved_non_message_directive(item_mapping):
            input_items.append(item)
            # Still counts as a normalization outcome: directive-only inputs
            # must default top-level ``instructions`` (to "") so the request
            # validates instead of failing on the required field.
            changed = True
            continue
        role = item_mapping.get("role")
        if role not in ("system", "developer"):
            input_items.append(item)
            continue
        instruction_text, preserved_content = _split_responses_instruction_item_content(item_mapping)
        if instruction_text:
            instruction_parts.append(instruction_text)
        if preserved_content is not None:
            preserved_item = dict(item_mapping)
            preserved_item["role"] = "user"
            preserved_item["content"] = preserved_content
            input_items.append(preserved_item)
        changed = True

    if not changed:
        return data

    normalized: MutableJsonObject = dict(data)
    existing_instructions = normalized.get("instructions")
    merged_instructions = _merge_responses_instructions(
        existing_instructions if isinstance(existing_instructions, str) else "",
        instruction_parts,
    )
    normalized["instructions"] = merged_instructions
    normalized["input"] = input_items
    return normalized


def _is_responses_lite_input(input_value: list[JsonValue]) -> bool:
    # Responses Lite requests carry their tool bundle as an input item with
    # type=additional_tools and deliberately place base instructions as a
    # developer message in input (with empty top-level instructions). That
    # shape is exactly what the lite upstream expects, so the instruction
    # lift must leave the whole request untouched.
    return any(
        (mapping := _json_mapping_or_none(item)) is not None and mapping.get("type") == "additional_tools"
        for item in input_value
    )


def responses_input_uses_lite_tools(input_value: JsonValue) -> bool:
    return is_json_list(input_value) and _is_responses_lite_input(input_value)


def _merge_responses_instructions(existing: str, extra_parts: list[str]) -> str:
    extra = "\n".join(part for part in extra_parts if part)
    if not extra:
        return existing
    if existing:
        return f"{existing}\n{extra}"
    return extra


def _split_responses_instruction_item_content(item: Mapping[str, JsonValue]) -> tuple[str, JsonValue | None]:
    content = item.get("content")
    if content is None:
        return "", None
    if isinstance(content, str):
        return content, None
    if is_json_list(content):
        instruction_parts: list[str] = []
        preserved_parts: list[JsonValue] = []
        for part in _json_parts(content):
            text = _responses_instruction_content_text(part)
            if text is not None:
                if text:
                    instruction_parts.append(text)
                continue
            preserved_parts.append(part)
        preserved_content: JsonValue | None = preserved_parts if preserved_parts else None
        return "\n".join(instruction_parts), preserved_content
    text = _responses_instruction_content_text(content)
    if text is not None:
        return text, None
    return "", content


def _responses_instruction_item_text(item: Mapping[str, JsonValue]) -> str:
    instruction_text, _ = _split_responses_instruction_item_content(item)
    return instruction_text


def _responses_instruction_content_text(content: JsonValue) -> str | None:
    if isinstance(content, str):
        return content
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is None:
        return None
    text = content_mapping.get("text")
    return text if isinstance(text, str) else None


def _sanitize_interleaved_reasoning_input_item(item: JsonValue) -> JsonValue | None:
    item_mapping = _json_mapping_or_none(item)
    if item_mapping is None:
        return item

    sanitized_item: MutableJsonObject = {}
    for key, value in item_mapping.items():
        if key in _INTERLEAVED_REASONING_KEYS:
            continue
        if key == "content":
            sanitized_content = _sanitize_interleaved_reasoning_content(value)
            if sanitized_content is None:
                continue
            sanitized_item[key] = sanitized_content
            continue
        sanitized_item[key] = value
    return sanitized_item


def _sanitize_interleaved_reasoning_content(content: JsonValue) -> JsonValue | None:
    if is_json_list(content):
        sanitized_parts: list[JsonValue] = []
        for part in _json_parts(content):
            sanitized_part = _sanitize_interleaved_reasoning_content_part(part)
            if sanitized_part is None:
                continue
            sanitized_parts.append(sanitized_part)
        return sanitized_parts
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is not None:
        return _sanitize_interleaved_reasoning_content_part(content_mapping)
    return content


def _sanitize_interleaved_reasoning_content_part(part: JsonValue) -> JsonValue | None:
    part_mapping = _json_mapping_or_none(part)
    if part_mapping is None:
        return part

    part_type = part_mapping.get("type")
    if isinstance(part_type, str) and part_type in _INTERLEAVED_REASONING_PART_TYPES:
        return None

    sanitized_part = dict(part_mapping)
    for key in _INTERLEAVED_REASONING_KEYS:
        sanitized_part.pop(key, None)
    return sanitized_part


def _normalize_role_input_item(value: JsonValue) -> JsonValue:
    value_mapping = _json_mapping_or_none(value)
    if value_mapping is None:
        return value
    role = value_mapping.get("role")
    if role == "assistant":
        return _normalize_assistant_input_item(value_mapping)
    if role == "tool":
        return _normalize_tool_input_item(value_mapping)
    return value


def _normalize_tool_input_item(value: Mapping[str, JsonValue]) -> JsonValue:
    tool_call_id = value.get("tool_call_id")
    tool_call_id_camel = value.get("toolCallId")
    call_id = value.get("call_id")
    resolved_call_id = tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None
    if resolved_call_id is None and isinstance(tool_call_id_camel, str) and tool_call_id_camel:
        resolved_call_id = tool_call_id_camel
    if resolved_call_id is None and isinstance(call_id, str) and call_id:
        resolved_call_id = call_id
    if not isinstance(resolved_call_id, str) or not resolved_call_id:
        raise ValueError("tool input items must include 'tool_call_id'")
    output = value.get("output")
    output_value = output if output is not None else value.get("content")
    return {
        "type": "function_call_output",
        "call_id": resolved_call_id,
        "output": _normalize_tool_output_value(output_value),
    }


def _normalize_tool_output_value(content: JsonValue) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if is_json_list(content):
        parts: list[str] = []
        for part in _json_parts(content):
            if isinstance(part, str):
                parts.append(part)
                continue
            extracted = _extract_text_content_part(part, _TOOL_TEXT_PART_TYPES)
            if extracted is not None:
                parts.append(extracted)
        if parts:
            return "".join(parts)
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    if is_json_mapping(content):
        extracted = _extract_text_content_part(content, _TOOL_TEXT_PART_TYPES)
        if extracted is not None:
            return extracted
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return str(content)


def _normalize_assistant_input_item(value: Mapping[str, JsonValue]) -> JsonValue:
    content = value.get("content")
    normalized_content = _normalize_assistant_content(content)
    if normalized_content == content:
        return value
    updated = dict(value)
    updated["content"] = normalized_content
    return updated


def _normalize_assistant_content(content: JsonValue) -> JsonValue:
    if content is None:
        return None
    if isinstance(content, str):
        return cast(JsonValue, [{"type": "output_text", "text": content}])
    if is_json_list(content):
        return cast(JsonValue, [_normalize_assistant_content_part(part) for part in _json_parts(content)])
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is not None:
        return [_normalize_assistant_content_part(content_mapping)]
    return content


def _normalize_assistant_content_part(part: JsonValue) -> JsonValue:
    if isinstance(part, str):
        return {"type": "output_text", "text": part}
    if not is_json_mapping(part):
        return part
    text = _extract_text_content_part(part, _ASSISTANT_TEXT_PART_TYPES)
    if text is not None:
        return {"type": "output_text", "text": text}
    return part


def _extract_text_content_part(part: JsonValue, allowed_types: frozenset[str]) -> str | None:
    part_mapping = _json_mapping_or_none(part)
    if part_mapping is None:
        return None
    part_type = part_mapping.get("type")
    text = part_mapping.get("text")
    if ((isinstance(part_type, str) and part_type in allowed_types) or part_type is None) and isinstance(text, str):
        return text
    refusal = part_mapping.get("refusal")
    if isinstance(part_type, str) and part_type == "refusal" and isinstance(refusal, str):
        return refusal
    return None


def _json_list_or_none(value: JsonValue) -> list[JsonValue] | None:
    if not is_json_list(value):
        return None
    return value


class ResponsesReasoning(BaseModel):
    model_config = ConfigDict(extra="allow")

    effort: str | None = None
    summary: str | None = None


class ResponsesTextFormat(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    type: str | None = None
    strict: bool | None = None
    schema_: JsonValue | None = Field(default=None, alias="schema")
    name: str | None = None


class ResponsesTextControls(BaseModel):
    model_config = ConfigDict(extra="allow")

    verbosity: str | None = None
    format: ResponsesTextFormat | None = None


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _move_input_instruction_messages(cls, data: JsonValue) -> JsonValue:
        return _normalize_responses_input_instructions(data)

    model: str = Field(min_length=1)
    instructions: str
    input: JsonValue
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | JsonObject | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    service_tier: str | None = None
    conversation: str | None = None
    previous_response_id: str | None = None
    truncation: str | None = None
    prompt_cache_key: str | None = None
    text: ResponsesTextControls | None = None

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        # ``input_file`` content items with a ``file_id`` are now allowed
        # and forwarded verbatim. They reference uploads registered via
        # ``POST /backend-api/files`` (see the file upload protocol),
        # which lets large attachments bypass the 16 MiB websocket
        # ceiling on `/responses`.
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            input_items = value
            return _sanitize_input_items(input_items)
        raise ValueError("input must be a string or array")

    @field_validator("include")
    @classmethod
    def _validate_include(cls, value: list[str]) -> list[str]:
        for entry in value:
            if entry not in _RESPONSES_INCLUDE_ALLOWLIST:
                raise ValueError(f"Unsupported include value: {entry}")
        return value

    @field_validator("truncation")
    @classmethod
    def _validate_truncation(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in {"auto", "disabled"}:
            raise ValueError("truncation must be 'auto' or 'disabled'")
        return value

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool | None) -> bool:
        return False

    @field_validator("previous_response_id")
    @classmethod
    def _normalize_previous_response_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[JsonValue]) -> list[JsonValue]:
        return validate_tool_types(value, allow_builtin_tools=True)

    @field_validator("tool_choice")
    @classmethod
    def _normalize_tool_choice_field(cls, value: JsonValue | None) -> JsonValue | None:
        return normalize_tool_choice(value)

    @field_validator("service_tier")
    @classmethod
    def _normalize_service_tier_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_service_tier_alias_value(value)
        return normalized if isinstance(normalized, str) else value

    @model_validator(mode="after")
    def _validate_conversation(self) -> "ResponsesRequest":
        if self.conversation and self.previous_response_id:
            raise ValueError("Provide either 'conversation' or 'previous_response_id', not both.")
        return self

    def model_dump_for_forwarding(self) -> MutableJsonObject:
        """Dump the request for re-serialization onto another wire.

        Like ``model_dump(mode="json", exclude_none=True)`` but without
        synthesizing fields the client never sent. Used by every path that
        forwards this request as a JSON body — the multi-instance owner
        forward (``HTTPBridgeOwnerClient``) and model-source Responses
        egress — so that field omission survives the hop and the receiving
        side does not re-mark ``tools`` as explicitly set.
        """
        payload: MutableJsonObject = self.model_dump(mode="json", exclude_none=True)
        if "tools" not in self.model_fields_set:
            # ``tools`` is declared with ``default_factory=list``, so
            # ``model_dump(exclude_none=True)`` synthesizes an explicit
            # ``"tools": []`` even when the client omitted the field. Codex
            # Responses-Lite clients omit top-level ``tools`` entirely (the
            # bundle rides in the ``additional_tools`` input item), and models
            # with reserved model tools (e.g. ``collaboration.spawn_agent`` on
            # gpt-5.6 ``multi_agent_version: v2``) reject any explicit
            # ``tools`` param that cannot match the reserved schema. Only
            # forward the field when the client actually sent it — including
            # an explicit client-sent ``[]``. See issue #1184.
            payload.pop("tools", None)
        return payload

    def to_payload(self) -> JsonObject:
        return _strip_unsupported_fields(self.model_dump_for_forwarding())


class ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _move_input_instruction_messages(cls, data: JsonValue) -> JsonValue:
        return _normalize_responses_input_instructions(data)

    model: str = Field(min_length=1)
    instructions: str
    input: JsonValue
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    service_tier: str | None = None
    prompt_cache_key: str | None = None

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        # ``input_file`` content items with a ``file_id`` are forwarded
        # verbatim; see ``ResponsesRequest._validate_input_type``.
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            input_items = value
            return _sanitize_input_items(input_items)
        raise ValueError("input must be a string or array")

    @model_validator(mode="before")
    @classmethod
    def _normalize_service_tier_aliases_before_validation(cls, data: JsonValue) -> JsonValue:
        if not is_json_mapping(data):
            return data
        normalized = dict(data)
        service_tier = normalized.get("service_tier")
        normalized_service_tier = _normalize_service_tier_alias_value(service_tier)
        if isinstance(normalized_service_tier, str):
            normalized["service_tier"] = normalized_service_tier
        return normalized

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool) -> bool:
        return False

    def to_payload(self) -> JsonObject:
        payload: MutableJsonObject = self.model_dump(mode="json", exclude_none=True)
        return _strip_compact_unsupported_fields(payload)


_UNSUPPORTED_UPSTREAM_FIELDS = {
    "max_output_tokens",
    "metadata",
    "prompt_cache_retention",
    "safety_identifier",
    "temperature",
    "top_p",
    "truncation",
    "user",
}

_POISONED_LOCAL_COMPACT_FALLBACK_TEXT = "Local compact fallback preserved the latest encrypted reasoning state."
_MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS = 100_000
_COMPACT_UPSTREAM_HEAD_ESTIMATED_TOKENS = 12_000
_ESTIMATED_CHARS_PER_TOKEN = 4


def _strip_unsupported_fields(payload: MutableJsonObject) -> MutableJsonObject:
    _normalize_openai_compatible_aliases(payload)
    _normalize_service_tier_aliases(payload)
    _sanitize_interleaved_reasoning_input(payload)
    _strip_poisoned_local_compact_fallback_items(payload)
    # ``tools`` is deliberately NOT canonicalized here: the wire payload must
    # forward client tool entries byte-preserved (array order, key order, and
    # unknown keys untouched) so reserved model tools survive upstream
    # byte/structural-equality checks. Order-insensitive canonicalization is
    # cache-affinity/observability-only; see ``canonicalized_tools``.
    for key in _UNSUPPORTED_UPSTREAM_FIELDS:
        payload.pop(key, None)
    return payload


def _strip_poisoned_local_compact_fallback_items(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value):
        return

    input_items = input_value
    kept: list[JsonValue] = []
    skip_next_poison_compaction = False
    changed = False
    for item in input_items:
        if skip_next_poison_compaction and is_json_mapping(item) and item.get("type") == "compaction":
            encrypted_content = item.get("encrypted_content")
            if isinstance(encrypted_content, str) and encrypted_content:
                skip_next_poison_compaction = False
                changed = True
                continue
        skip_next_poison_compaction = False

        if _is_poisoned_local_compact_fallback_message(item):
            skip_next_poison_compaction = True
            changed = True
            continue

        kept.append(item)

    if changed:
        payload["input"] = kept


def _is_poisoned_local_compact_fallback_message(item: JsonValue) -> bool:
    if not is_json_mapping(item):
        return False
    if item.get("type") != "message" or item.get("role") != "assistant":
        return False
    content = item.get("content")
    if not is_json_list(content):
        return False
    for part in content:
        if not is_json_mapping(part):
            continue
        if part.get("text") == _POISONED_LOCAL_COMPACT_FALLBACK_TEXT:
            return True
    return False


def canonicalized_tools(tools: list[JsonValue]) -> list[JsonValue]:
    """Return an order- and key-order-insensitive canonical form of ``tools``.

    Used only for prompt-cache affinity/observability hashing (change #228):
    two requests that differ solely in tool array order or object key order
    hash identically. The result MUST NOT feed the upstream wire payload —
    outgoing requests forward the client's tool entries byte-preserved (see
    issue #1184). Array values (e.g. ``parameters.required``) are never
    reordered; only mapping keys are sorted.
    """
    sorted_tools = sorted(tools, key=_tool_sort_key)
    return [_sort_keys_recursive(t) for t in sorted_tools]


def _tool_sort_key(tool: JsonValue) -> str:
    if not is_json_mapping(tool):
        return ""
    tool_map = tool
    name = tool_map.get("name")
    if isinstance(name, str):
        return name
    func = tool_map.get("function")
    if is_json_mapping(func):
        func_name = func.get("name")
        if isinstance(func_name, str):
            return func_name
    return ""


def _sort_keys_recursive(value: JsonValue) -> JsonValue:
    if is_json_mapping(value):
        mapping = value
        return {k: _sort_keys_recursive(v) for k, v in sorted(mapping.items())}
    if is_json_list(value):
        return [_sort_keys_recursive(item) for item in value]
    return value


def _strip_compact_unsupported_fields(payload: MutableJsonObject) -> MutableJsonObject:
    payload = _strip_unsupported_fields(payload)
    normalized_payload = _normalize_responses_input_instructions(payload)
    if is_json_mapping(normalized_payload):
        payload = dict(normalized_payload)
    _trim_compact_input_for_upstream(payload)
    payload.pop("store", None)
    payload.pop("text", None)
    payload.pop("tools", None)
    payload.pop("tool_choice", None)
    payload.pop("client_metadata", None)
    payload["parallel_tool_calls"] = False
    return payload


def _trim_compact_input_for_upstream(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value):
        return
    token_counts = [_estimated_json_array_item_tokens(item) for item in input_value]
    total_tokens = _estimated_json_tokens(input_value)
    if total_tokens <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        return

    head_count = _compact_trim_prefix_count(token_counts)
    state_anchor_indices = _compact_state_anchor_indices(input_value)
    marker_tokens = _estimated_json_array_item_tokens(_compact_trim_marker(omitted_items=0, omitted_tokens=0))
    wire_budget = max(0, _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS - marker_tokens)

    side_effect_indices = _compact_side_effect_anchor_indices(input_value)
    unusable_side_effect_indices = {
        index
        for index, item in enumerate(input_value)
        if is_json_mapping(item)
        and _compact_item_is_side_effect_anchor(item)
        and (not isinstance(item.get("call_id"), str) or not item["call_id"])
    }
    # Keep priority side effects as complete call/output units before spending
    # the remaining budget on ordinary head/tail context.  Otherwise a large
    # recent message can leave room for a call but not its output, causing
    # reconciliation to drop the historical side effect that would fit after
    # trimming that ordinary message.
    side_effect_indices = _compact_reconciled_tool_call_indices(
        input_value,
        side_effect_indices,
        token_counts=token_counts,
        token_budget=sum(token_counts),
    )
    required_indices = set(state_anchor_indices)
    if input_value:
        required_indices.add(len(input_value) - 1)
    if required_indices & unusable_side_effect_indices:
        raise ClientPayloadError(
            "Compact input cannot retain a required side-effect call without a usable call_id.",
            param="input",
            code="responses_compact_input_too_large",
        )
    required_indices = _compact_reconciled_tool_call_indices(
        input_value,
        required_indices,
        token_counts=token_counts,
        token_budget=sum(token_counts),
        required_indices=required_indices,
    )
    required_input = _compact_trimmed_input_with_markers(input_value, token_counts, required_indices)
    required_tokens = _estimated_json_tokens(required_input)
    if required_tokens > _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        raise ClientPayloadError(
            "Compact input exceeds the upstream size limit and cannot be trimmed "
            "without removing required state anchors.",
            param="input",
            code="responses_compact_input_too_large",
        )
    side_effect_indices &= _compact_reconciled_tool_call_indices(
        input_value,
        required_indices | side_effect_indices,
        token_counts=token_counts,
        token_budget=wire_budget,
        required_indices=required_indices,
    )
    selected_indices = set(state_anchor_indices)
    selected_indices.update(side_effect_indices)
    selected_indices.update(range(head_count))
    selected_tokens = sum(token_counts[index] for index in selected_indices)
    tail_budget = max(0, _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS - selected_tokens - marker_tokens)
    selected_indices.update(
        _compact_trim_suffix_indices(
            token_counts,
            selected_indices=selected_indices,
            start_index=head_count,
            token_budget=tail_budget,
        )
    )
    selected_indices.update(required_indices)
    selected_indices.difference_update(unusable_side_effect_indices)
    selected_indices = _compact_reconciled_tool_call_indices(
        input_value,
        selected_indices,
        token_counts=token_counts,
        token_budget=max(0, _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS - marker_tokens),
        required_indices=required_indices | side_effect_indices,
    )
    selected_indices = _compact_fit_selected_indices_to_wire_budget(
        input_value,
        token_counts,
        selected_indices=selected_indices,
        required_indices=required_indices,
        priority_indices=side_effect_indices,
    )
    trimmed_input = (
        input_value
        if len(selected_indices) == len(input_value)
        else _compact_trimmed_input_with_markers(input_value, token_counts, selected_indices)
    )
    trimmed_tokens = _estimated_json_tokens(trimmed_input)
    if trimmed_tokens > _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        raise ClientPayloadError(
            "Compact input still exceeds the upstream size limit after retaining required compact context.",
            param="input",
            code="responses_compact_input_too_large",
        )
    if trimmed_input is input_value:
        return
    payload["input"] = trimmed_input


def _compact_fit_selected_indices_to_wire_budget(
    input_value: list[JsonValue],
    token_counts: list[int],
    *,
    selected_indices: set[int],
    required_indices: set[int],
    priority_indices: set[int] | None = None,
) -> set[int]:
    """Drop best-effort middle context until the exact serialized input fits."""

    selected = set(selected_indices)
    prioritized = priority_indices or set()

    def optional_drop_key(index: int) -> tuple[int, int, int]:
        if index in prioritized:
            return (0, 0, -index)
        return (1, min(index, len(input_value) - 1 - index), index)

    optional_indices = sorted(
        selected - required_indices,
        key=optional_drop_key,
        reverse=True,
    )
    marker_budget = max(
        0,
        _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS
        - _estimated_json_array_item_tokens(_compact_trim_marker(omitted_items=0, omitted_tokens=0)),
    )
    for index in optional_indices:
        candidate_input = _compact_trimmed_input_with_markers(input_value, token_counts, selected)
        if _estimated_json_tokens(candidate_input) <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
            break
        if index not in selected:
            continue
        trial = set(selected)
        trial.remove(index)
        trial = _compact_reconciled_tool_call_indices(
            input_value,
            trial,
            token_counts=token_counts,
            token_budget=marker_budget,
            required_indices=required_indices,
            allow_pair_additions=False,
        )
        if required_indices <= trial and trial != selected:
            selected = trial
    return selected


def validate_compact_input_wire_budget(payload: Mapping[str, JsonValue]) -> None:
    """Reject compact input that exceeds the upstream budget after transformations."""

    input_value = payload.get("input")
    if not is_json_list(input_value):
        return
    if _estimated_json_tokens(input_value) <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        return
    raise ClientPayloadError(
        "Compact input exceeds the upstream size limit after preparing the final wire payload.",
        param="input",
        code="responses_compact_input_too_large",
        error_type="invalid_request_error",
    )


def _compact_state_anchor_indices(input_value: list[JsonValue]) -> set[int]:
    preserved_indices: set[int] = set()
    for index, item in enumerate(input_value):
        if not is_json_mapping(item):
            continue
        item_mapping = item
        if item_mapping.get("type") == "additional_tools":
            preserved_indices.add(index)
            developer_index = index + 1
            if developer_index < len(input_value):
                developer_item = input_value[developer_index]
                if is_json_mapping(developer_item) and developer_item.get("role") == "developer":
                    developer_type = developer_item.get("type")
                    if developer_type is None or developer_type == "message":
                        preserved_indices.add(developer_index)
        if _is_preserved_non_message_directive(item_mapping):
            preserved_indices.add(index)
        if _compact_item_is_state_anchor(item_mapping):
            preserved_indices.add(index)
    return preserved_indices


def _compact_side_effect_anchor_indices(input_value: list[JsonValue]) -> set[int]:
    preserved_indices: set[int] = set()
    for index, item in enumerate(input_value):
        if (
            is_json_mapping(item)
            and _compact_item_is_side_effect_anchor(item)
            and isinstance(item.get("call_id"), str)
            and item["call_id"]
        ):
            preserved_indices.add(index)
    return preserved_indices


def _compact_reconciled_tool_call_indices(
    input_value: list[JsonValue],
    selected_indices: set[int],
    *,
    token_counts: list[int],
    token_budget: int,
    required_indices: set[int] | None = None,
    allow_pair_additions: bool = True,
) -> set[int]:
    call_indices_by_id: dict[str, list[int]] = {}
    output_indices_by_id: dict[str, list[int]] = {}
    for index, item in enumerate(input_value):
        if not is_json_mapping(item):
            continue
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        item_type = item.get("type")
        if item_type in _COMPACT_TOOL_CALL_ITEM_TYPES:
            call_indices_by_id.setdefault(call_id, []).append(index)
        elif item_type in _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES:
            output_indices_by_id.setdefault(call_id, []).append(index)

    reconciled = set(selected_indices)
    protected_indices = required_indices or set()
    selected_tokens = sum(token_counts[index] for index in reconciled)

    def add_indices(indices: Iterable[int]) -> bool:
        nonlocal selected_tokens
        missing_indices = [index for index in indices if index not in reconciled]
        missing_tokens = sum(token_counts[index] for index in missing_indices)
        if selected_tokens + missing_tokens > token_budget:
            return False
        reconciled.update(missing_indices)
        selected_tokens += missing_tokens
        return True

    def remove_indices(indices: Iterable[int]) -> None:
        nonlocal selected_tokens
        for index in indices:
            if index in reconciled and index not in protected_indices:
                reconciled.remove(index)
                selected_tokens -= token_counts[index]

    def matching_call_index(call_indices: list[int], output_index: int) -> int | None:
        if not call_indices:
            return None
        preceding_call_indices = [call_index for call_index in call_indices if call_index < output_index]
        if preceding_call_indices:
            return preceding_call_indices[-1]
        return call_indices[0]

    def matching_output_indices(call_indices: list[int], call_index: int, output_indices: list[int]) -> list[int]:
        next_call_indices = [next_call_index for next_call_index in call_indices if next_call_index > call_index]
        next_call_index = next_call_indices[0] if next_call_indices else None
        return [
            output_index
            for output_index in output_indices
            if output_index > call_index and (next_call_index is None or output_index < next_call_index)
        ]

    for call_id, output_indices in output_indices_by_id.items():
        selected_outputs = [index for index in output_indices if index in reconciled]
        if not selected_outputs:
            continue
        call_indices = call_indices_by_id.get(call_id, [])
        for output_index in selected_outputs:
            call_index = matching_call_index(call_indices, output_index)
            if call_index is None:
                remove_indices([output_index])
            elif not allow_pair_additions and call_index not in reconciled:
                remove_indices([output_index])
            elif not add_indices([call_index]):
                remove_indices([output_index])
    for call_id, call_indices in call_indices_by_id.items():
        output_indices = output_indices_by_id.get(call_id, [])
        for call_index in call_indices:
            if call_index not in reconciled:
                continue
            matched_output_indices = matching_output_indices(call_indices, call_index, output_indices)
            if not matched_output_indices:
                remove_indices([call_index])
                continue
            if not allow_pair_additions and any(index not in reconciled for index in matched_output_indices):
                remove_indices([call_index, *matched_output_indices])
                continue
            if not add_indices(matched_output_indices):
                remove_indices([call_index, *matched_output_indices])
    return reconciled


def _compact_item_is_state_anchor(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    if item_type == "additional_tools":
        return True
    if item_type in {None, "message"} and item.get("role") in {"system", "developer"}:
        return True
    if item_type == "function_call":
        name = item.get("name")
        if isinstance(name, str) and name in _COMPACT_STATE_TOOL_NAMES:
            return True
        function = item.get("function")
        if is_json_mapping(function):
            function_name = function.get("name")
            if isinstance(function_name, str) and function_name in _COMPACT_STATE_TOOL_NAMES:
                return True
    for text in _compact_item_texts(item):
        stripped = text.lstrip()
        if stripped.startswith(_GOAL_CONTINUATION_CONTEXT_PREFIX):
            return True
        if stripped.startswith(_PLAN_MODE_CONTEXT_PREFIX):
            return True
    return False


def _compact_item_is_side_effect_anchor(item: Mapping[str, JsonValue]) -> bool:
    return is_downstream_side_effect_tool_call_item(item)


def _compact_item_texts(item: Mapping[str, JsonValue]) -> list[str]:
    content = item.get("content")
    if isinstance(content, str):
        return [content]
    if is_json_mapping(content):
        content_parts: list[JsonValue] = [content]
    elif is_json_list(content):
        content_parts = content
    else:
        return []

    texts: list[str] = []
    for part in content_parts:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not is_json_mapping(part):
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
    return texts


def _compact_trimmed_input_with_markers(
    input_value: list[JsonValue], token_counts: list[int], selected_indices: set[int]
) -> list[JsonValue]:
    trimmed: list[JsonValue] = []
    omitted_items = 0
    omitted_tokens = 0
    for index, item in enumerate(input_value):
        if index in selected_indices:
            if omitted_items:
                trimmed.append(_compact_trim_marker(omitted_items=omitted_items, omitted_tokens=omitted_tokens))
                omitted_items = 0
                omitted_tokens = 0
            trimmed.append(item)
        else:
            omitted_items += 1
            omitted_tokens += token_counts[index]
    if omitted_items:
        trimmed.append(_compact_trim_marker(omitted_items=omitted_items, omitted_tokens=omitted_tokens))
    return trimmed


def _compact_trim_prefix_count(token_counts: list[int]) -> int:
    used = 0
    count = 0
    for token_count in token_counts:
        if used + token_count > _COMPACT_UPSTREAM_HEAD_ESTIMATED_TOKENS:
            break
        used += token_count
        count += 1
    return count


def _compact_trim_suffix_indices(
    token_counts: list[int], *, selected_indices: set[int], start_index: int, token_budget: int
) -> set[int]:
    used = 0
    indices: set[int] = set()
    for index in range(len(token_counts) - 1, start_index - 1, -1):
        if index in selected_indices:
            continue
        token_count = token_counts[index]
        if indices and used + token_count > token_budget:
            break
        if not indices and token_count > token_budget:
            indices.add(index)
            break
        used += token_count
        indices.add(index)
    return indices


def _compact_trim_marker(*, omitted_items: int, omitted_tokens: int) -> JsonObject:
    return {
        "type": "message",
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": (
                    "[compact trim] Omitted "
                    f"{omitted_items} input items (~{omitted_tokens} estimated tokens) "
                    "before forwarding this oversized compact request upstream. The initial "
                    "context, most recent context, and compact state anchors were preserved."
                ),
            }
        ],
    }


def _estimated_json_tokens(value: JsonValue) -> int:
    # Match stdlib/aiohttp's default JSON wire escaping and separators. Calling
    # this on the whole input list also counts brackets, commas, and inter-item
    # whitespace, so many individually small items cannot bypass the cap.
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return max(1, (len(serialized) + _ESTIMATED_CHARS_PER_TOKEN - 1) // _ESTIMATED_CHARS_PER_TOKEN)


def _estimated_json_array_item_tokens(value: JsonValue) -> int:
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True)
    # Every retained item also needs the array's comma-space delimiter. The
    # final whole-array validation below accounts exactly for brackets and the
    # missing delimiter after the last item.
    wire_chars = len(serialized) + 2
    return max(1, (wire_chars + _ESTIMATED_CHARS_PER_TOKEN - 1) // _ESTIMATED_CHARS_PER_TOKEN)


def _sanitize_interleaved_reasoning_input(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    input_items = _json_list_or_none(input_value)
    if input_items is None:
        return
    payload["input"] = _sanitize_input_items(input_items)


def normalize_reasoning_aliases(payload: MutableJsonObject) -> None:
    reasoning_effort = payload.pop("reasoningEffort", None)
    reasoning_summary = payload.pop("reasoningSummary", None)
    provider_thinking = payload.pop("thinking", None)
    provider_enable_thinking = payload.pop("enable_thinking", None)

    reasoning_payload = _json_mapping_or_none(payload.get("reasoning"))
    if reasoning_payload is not None:
        reasoning_map: MutableJsonObject = dict(reasoning_payload.items())
    else:
        reasoning_map = {}

    if isinstance(reasoning_effort, str) and "effort" not in reasoning_map:
        reasoning_map["effort"] = reasoning_effort
    if isinstance(reasoning_summary, str) and "summary" not in reasoning_map:
        reasoning_map["summary"] = reasoning_summary

    provider_reasoning = _normalize_thinking_alias(
        provider_thinking,
        enable_thinking=provider_enable_thinking,
    )
    if provider_reasoning is not None:
        if "effort" not in reasoning_map and "effort" in provider_reasoning:
            reasoning_map["effort"] = provider_reasoning["effort"]
        if "summary" not in reasoning_map and "summary" in provider_reasoning:
            reasoning_map["summary"] = provider_reasoning["summary"]

    if reasoning_map:
        payload["reasoning"] = reasoning_map


def _normalize_thinking_alias(
    thinking: JsonValue,
    *,
    enable_thinking: JsonValue,
) -> MutableJsonObject | None:
    if isinstance(thinking, bool):
        return {"effort": "medium"} if thinking else None
    if isinstance(thinking, str):
        normalized = thinking.strip().lower()
        if normalized in {"low", "medium", "high", "xhigh", "max", "ultra"}:
            return {"effort": normalized}
        if normalized in {"enabled", "true", "on"}:
            return {"effort": "medium"}
        if normalized in {"disabled", "false", "off"}:
            return None
    thinking_mapping = _json_mapping_or_none(thinking)
    if thinking_mapping is not None:
        normalized: MutableJsonObject = {}
        effort = thinking_mapping.get("effort")
        summary = thinking_mapping.get("summary")
        if isinstance(effort, str) and effort.strip():
            normalized["effort"] = effort.strip().lower()
        if isinstance(summary, str) and summary.strip():
            normalized["summary"] = summary.strip()
        if normalized:
            return normalized
        thinking_type = thinking_mapping.get("type")
        if isinstance(thinking_type, str):
            normalized_type = thinking_type.strip().lower()
            if normalized_type == "enabled":
                return {"effort": "medium"}
            if normalized_type == "disabled":
                return None
        enabled = thinking_mapping.get("enabled")
        if isinstance(enabled, bool):
            return {"effort": "medium"} if enabled else None

    if isinstance(enable_thinking, bool):
        return {"effort": "medium"} if enable_thinking else None
    return None


def _normalize_openai_compatible_aliases(payload: MutableJsonObject) -> None:
    text_verbosity = payload.pop("textVerbosity", None)
    top_level_verbosity = payload.pop("verbosity", None)
    prompt_cache_key = payload.pop("promptCacheKey", None)
    prompt_cache_retention = payload.pop("promptCacheRetention", None)

    if isinstance(prompt_cache_key, str) and "prompt_cache_key" not in payload:
        payload["prompt_cache_key"] = prompt_cache_key
    if isinstance(prompt_cache_retention, str) and "prompt_cache_retention" not in payload:
        payload["prompt_cache_retention"] = prompt_cache_retention

    normalize_reasoning_aliases(payload)

    text_payload = _json_mapping_or_none(payload.get("text"))
    if text_payload is not None:
        text_map: MutableJsonObject = dict(text_payload.items())
    else:
        text_map = {}

    if isinstance(text_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = text_verbosity
    if isinstance(top_level_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = top_level_verbosity
    if text_map:
        payload["text"] = text_map


def _normalize_service_tier_aliases(payload: MutableJsonObject) -> None:
    service_tier = payload.get("service_tier")
    normalized = _normalize_service_tier_alias_value(service_tier)
    if isinstance(normalized, str):
        payload["service_tier"] = normalized


def _normalize_service_tier_alias_value(value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        return value
    if value.strip().lower() == "fast":
        return "priority"
    return value


def _normalize_input_text(text: str) -> list[JsonValue]:
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]
