from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from app.core.openai.contracts import (
    FunctionCallInputItem,
    FunctionCallOutputInputItem,
    InputFileItem,
    OpenAIMessage,
    RefusalContentPart,
    TextContentPart,
)
from app.core.openai.exceptions import ClientPayloadError
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_dict, is_json_list

_SUPPORTED_MESSAGE_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})


def _json_dict_or_none(value: JsonValue) -> dict[str, JsonValue] | None:
    if not is_json_dict(value):
        return None
    return value


def _content_parts(content: JsonValue) -> list[JsonValue]:
    if is_json_list(content):
        return content
    return [content]


def coerce_messages(existing_instructions: str, messages: Sequence[JsonValue]) -> tuple[str, list[JsonValue]]:
    instruction_parts: list[str] = []
    input_messages: list[JsonValue] = []
    for message in messages:
        message_dict = _json_dict_or_none(message)
        if message_dict is None:
            raise ClientPayloadError("Each message must be an object.", param="messages")
        role_value = message_dict.get("role")
        role = role_value if isinstance(role_value, str) else None
        if role is None:
            raise ClientPayloadError("Each message must include a string 'role'.", param="messages")
        if role not in _SUPPORTED_MESSAGE_ROLES:
            raise ClientPayloadError(f"Unsupported message role: {role}", param="messages")
        if role in ("system", "developer"):
            _ensure_text_only_content(message_dict.get("content"), role)
            content_text = _content_to_text(message_dict.get("content"))
            if content_text:
                instruction_parts.append(content_text)
            continue
        if role == "tool":
            input_messages.append(cast(JsonValue, _convert_tool_message(cast(OpenAIMessage, message_dict))))
            continue
        if role == "assistant":
            tool_calls = message_dict.get("tool_calls")
            if is_json_list(tool_calls) and tool_calls:
                input_messages.extend(_decompose_assistant_tool_calls(cast(OpenAIMessage, message_dict)))
            else:
                input_messages.append(cast(JsonValue, _normalize_message_content(cast(OpenAIMessage, message_dict))))
            continue
        input_messages.append(cast(JsonValue, _normalize_message_content(cast(OpenAIMessage, message_dict))))
    merged = _merge_instructions(existing_instructions, instruction_parts)
    return merged, input_messages


def _merge_instructions(existing: str, extra_parts: list[str]) -> str:
    if not extra_parts:
        return existing
    extra = "\n".join([part for part in extra_parts if part])
    if not extra:
        return existing
    if existing:
        return f"{existing}\n{extra}"
    return extra


def _content_to_text(content: JsonValue) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if is_json_list(content):
        parts: list[str] = []
        for part in _content_parts(content):
            if isinstance(part, str):
                parts.append(part)
            else:
                part_dict = _json_dict_or_none(part)
                if part_dict is None:
                    continue
                text = part_dict.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join([part for part in parts if part])
    content_dict = _json_dict_or_none(content)
    if content_dict is not None:
        text = content_dict.get("text")
        if isinstance(text, str):
            return text
        return None
    return None


def _ensure_text_only_content(content: JsonValue, role: str) -> None:
    if content is None:
        return
    if isinstance(content, str):
        return
    if is_json_list(content):
        for part in _content_parts(content):
            if isinstance(part, str):
                continue
            part_dict = _json_dict_or_none(part)
            if part_dict is not None:
                part_type = part_dict.get("type")
                if part_type not in (None, "text"):
                    raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")
                text = part_dict.get("text")
                if isinstance(text, str):
                    continue
            raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")
        return
    content_dict = _json_dict_or_none(content)
    if content_dict is not None:
        part_type = content_dict.get("type")
        if part_type not in (None, "text"):
            raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")
        text = content_dict.get("text")
        if isinstance(text, str):
            return
    raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")


def _decompose_assistant_tool_calls(message: OpenAIMessage) -> list[JsonValue]:
    items: list[JsonValue] = []
    content = message.get("content")
    refusal = _get_assistant_refusal(message)
    if content is not None or refusal is not None:
        parts = _to_content_list(_normalize_content_parts(content, "assistant")) if content is not None else []
        if refusal is not None:
            parts.append(RefusalContentPart(type="refusal", refusal=refusal))
        msg_item: OpenAIMessage = {"role": "assistant", "content": parts}
        items.append(cast(JsonValue, msg_item))
    tool_calls = message.get("tool_calls")
    if is_json_list(tool_calls):
        for tc in _content_parts(tool_calls):
            tc_dict = _json_dict_or_none(tc)
            if tc_dict is None:
                raise ClientPayloadError("tool_calls entries must be objects.", param="messages")
            call_id = tc_dict.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise ClientPayloadError("tool_calls[].id is required.", param="messages")
            function = _json_dict_or_none(tc_dict.get("function"))
            if function is None:
                raise ClientPayloadError("tool_calls[].function is required.", param="messages")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise ClientPayloadError("tool_calls[].function.name is required.", param="messages")
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                raise ClientPayloadError(
                    "tool_calls[].function.arguments must be a string.",
                    param="messages",
                )
            items.append(
                cast(
                    JsonValue,
                    FunctionCallInputItem(type="function_call", call_id=call_id, name=name, arguments=arguments),
                )
            )
    return items


def _convert_tool_message(message: OpenAIMessage) -> FunctionCallOutputInputItem:
    tool_call_id = message.get("tool_call_id")
    tool_call_id_camel = message.get("toolCallId")
    call_id = message.get("call_id")
    resolved_call_id = tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None
    if resolved_call_id is None and isinstance(tool_call_id_camel, str) and tool_call_id_camel:
        resolved_call_id = tool_call_id_camel
    if resolved_call_id is None and isinstance(call_id, str) and call_id:
        resolved_call_id = call_id
    if not isinstance(resolved_call_id, str) or not resolved_call_id:
        raise ClientPayloadError("tool messages must include 'tool_call_id'.", param="messages")
    content = message.get("content")
    if isinstance(content, str):
        output = content
    elif is_json_list(content):
        output = _concat_text_parts(content)
        if not output and content:
            raise ClientPayloadError(
                "tool message content array contains no valid text parts.",
                param="messages",
            )
    elif content is None:
        raise ClientPayloadError("tool message content is required.", param="messages")
    else:
        raise ClientPayloadError(
            "tool message content must be a string or array.",
            param="messages",
        )
    return FunctionCallOutputInputItem(type="function_call_output", call_id=resolved_call_id, output=output)


def _concat_text_parts(content: list[JsonValue]) -> str:
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        else:
            part_dict = _json_dict_or_none(part)
            if part_dict is None:
                continue
            text = part_dict.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _normalize_message_content(message: OpenAIMessage) -> OpenAIMessage:
    content = message.get("content")
    role = message.get("role")
    role_str = role if isinstance(role, str) else "user"
    refusal = _get_assistant_refusal(message) if role_str == "assistant" else None

    # The Responses API input message item only carries `role` + `content`.
    # Build a fresh dict with exactly those keys so unknown message-object
    # fields (the standard chat `name` field, client-internal bookkeeping
    # keys, etc.) are never forwarded upstream — OpenAI's own
    # `/v1/chat/completions` ignores them rather than relaying them, and
    # the upstream Responses API rejects the whole request if it sees them.
    if content is None and refusal is None:
        if "content" in message:
            return cast(OpenAIMessage, {"role": role, "content": content})
        return cast(OpenAIMessage, {"role": role})

    if content is not None:
        normalized = _normalize_content_parts(content, role_str)
    else:
        normalized = []
    if refusal is not None:
        parts = _to_content_list(normalized)
        parts.append({"type": "refusal", "refusal": refusal})
        normalized = parts
    return cast(OpenAIMessage, {"role": role, "content": normalized})


def _get_assistant_refusal(message: OpenAIMessage) -> str | None:
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        return refusal
    return None


def _to_content_list(normalized: JsonValue) -> list[JsonValue]:
    if is_json_list(normalized):
        return normalized
    if normalized is None or normalized == "":
        return []
    return [normalized]


def _text_type_for_role(role: str) -> str:
    return "output_text" if role == "assistant" else "input_text"


def _normalize_content_parts(content: JsonValue, role: str = "user") -> JsonValue:
    if content is None:
        return None
    text_type = _text_type_for_role(role)
    if isinstance(content, str):
        return cast(JsonValue, [TextContentPart(type=text_type, text=content)])
    normalized_parts: list[JsonValue] = []
    for part in _content_parts(content):
        if isinstance(part, str):
            normalized_parts.append(cast(JsonValue, TextContentPart(type=text_type, text=part)))
            continue
        part_dict = _json_dict_or_none(part)
        if part_dict is None:
            normalized_parts.append(part)
            continue
        normalized_parts.append(_normalize_content_part(part_dict, role))
    if is_json_list(content):
        return normalized_parts
    return normalized_parts[0] if normalized_parts else ""


def _normalize_content_part(part: dict[str, JsonValue], role: str = "user") -> JsonValue:
    part_type = part.get("type") or ("text" if "text" in part else None)
    text_type = _text_type_for_role(role)
    if part_type in ("text", "input_text", "output_text"):
        text = part.get("text")
        if isinstance(text, str):
            return cast(JsonValue, TextContentPart(type=text_type, text=text))
        return part
    if role == "assistant":
        return part
    if part_type == "image_url":
        image_url = part.get("image_url")
        detail: str | None = None
        if isinstance(image_url, dict):
            url = image_url.get("url")
            detail_value = image_url.get("detail")
            if isinstance(detail_value, str):
                detail = detail_value
        elif isinstance(image_url, str):
            url = image_url
        else:
            url = None
        if isinstance(url, str):
            return {"type": "input_image", "image_url": url, **({"detail": detail} if detail is not None else {})}
        return part
    if part_type == "input_image":
        return part
    if part_type == "input_audio":
        data_url = _audio_input_to_data_url(part.get("input_audio"))
        if data_url:
            return {"type": "input_file", "file_url": data_url}
        return part
    if part_type == "file":
        return cast(JsonValue, _file_part_to_input_file(part.get("file")))
    return part


def _audio_input_to_data_url(input_audio: JsonValue) -> str | None:
    input_audio_dict = _json_dict_or_none(input_audio)
    if input_audio_dict is None:
        return None
    data = input_audio_dict.get("data")
    audio_format = input_audio_dict.get("format")
    if not isinstance(data, str) or not isinstance(audio_format, str):
        return None
    mime_type = _audio_mime_type(audio_format)
    return f"data:{mime_type};base64,{data}"


def _audio_mime_type(audio_format: str) -> str:
    if audio_format == "wav":
        return "audio/wav"
    if audio_format == "mp3":
        return "audio/mpeg"
    return f"audio/{audio_format}"


def _file_part_to_input_file(file_info: JsonValue) -> InputFileItem:
    file_info_dict = _json_dict_or_none(file_info)
    if file_info_dict is None:
        return InputFileItem(type="input_file")
    file_id = file_info_dict.get("file_id")
    if isinstance(file_id, str) and file_id:
        return InputFileItem(type="input_file", file_id=file_id)
    file_url = file_info_dict.get("file_url")
    if isinstance(file_url, str) and file_url:
        return InputFileItem(type="input_file", file_url=file_url)
    file_data = file_info_dict.get("file_data")
    if not isinstance(file_data, str):
        data = file_info_dict.get("data")
        file_data = data if isinstance(data, str) else None
    if isinstance(file_data, str):
        mime_type = file_info_dict.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = file_info_dict.get("content_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = "application/octet-stream"
        return InputFileItem(type="input_file", file_url=f"data:{mime_type};base64,{file_data}")
    return InputFileItem(type="input_file")
