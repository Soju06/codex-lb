from __future__ import annotations

from collections.abc import Sequence

from app.core.openai.exceptions import ClientPayloadError
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_dict, is_json_list


def coerce_messages(existing_instructions: str, messages: Sequence[JsonValue]) -> tuple[str, list[JsonValue]]:
    instruction_parts: list[str] = []
    input_messages: list[JsonValue] = []
    for message in messages:
        if not is_json_dict(message):
            raise ClientPayloadError("Each message must be an object.", param="messages")
        role_value = message.get("role")
        role = role_value if isinstance(role_value, str) else None
        if role in ("system", "developer"):
            _ensure_text_only_content(message.get("content"), role)
            content_text = _content_to_text(message.get("content"))
            if content_text:
                instruction_parts.append(content_text)
            continue
        input_messages.append(_normalize_message_content(message))
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
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif is_json_dict(part):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join([part for part in parts if part])
    if is_json_dict(content):
        text = content.get("text")
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
        for part in content:
            if isinstance(part, str):
                continue
            if is_json_dict(part):
                part_type = part.get("type")
                if part_type not in (None, "text"):
                    raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")
                text = part.get("text")
                if isinstance(text, str):
                    continue
            raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")
        return
    if is_json_dict(content):
        part_type = content.get("type")
        if part_type not in (None, "text"):
            raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")
        text = content.get("text")
        if isinstance(text, str):
            return
    raise ClientPayloadError(f"{role} messages must be text-only.", param="messages")


def _normalize_message_content(message: dict[str, JsonValue]) -> dict[str, JsonValue]:
    content = message.get("content")
    if content is None:
        return message
    normalized = _normalize_content_parts(content)
    if normalized is content:
        return message
    updated = dict(message)
    updated["content"] = normalized
    return updated


def _normalize_content_parts(content: JsonValue) -> JsonValue:
    if content is None:
        return content
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    parts = content if is_json_list(content) else [content]
    normalized_parts: list[JsonValue] = []
    for part in parts:
        if isinstance(part, str):
            normalized_parts.append({"type": "input_text", "text": part})
            continue
        if not is_json_dict(part):
            normalized_parts.append(part)
            continue
        normalized_parts.append(_normalize_content_part(part))
    if is_json_list(content):
        return normalized_parts
    return normalized_parts[0] if normalized_parts else ""


def _normalize_content_part(part: dict[str, JsonValue]) -> JsonValue:
    part_type = part.get("type") or ("text" if "text" in part else None)
    if part_type in ("text", "input_text"):
        text = part.get("text")
        if isinstance(text, str):
            return {"type": "input_text", "text": text}
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
            normalized: dict[str, JsonValue] = {"type": "input_image", "image_url": url}
            if detail is not None:
                normalized["detail"] = detail
            return normalized
        return part
    if part_type == "input_image":
        return part
    if part_type == "input_audio":
        data_url = _audio_input_to_data_url(part.get("input_audio"))
        if data_url:
            return {"type": "input_file", "file_url": data_url}
        return part
    if part_type == "file":
        return _file_part_to_input_file(part.get("file"))
    return part


def _audio_input_to_data_url(input_audio: JsonValue) -> str | None:
    if not is_json_dict(input_audio):
        return None
    data = input_audio.get("data")
    audio_format = input_audio.get("format")
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


def _file_part_to_input_file(file_info: JsonValue) -> dict[str, JsonValue]:
    if not is_json_dict(file_info):
        return {"type": "input_file"}
    file_id = file_info.get("file_id")
    if isinstance(file_id, str) and file_id:
        return {"type": "input_file", "file_id": file_id}
    file_url = file_info.get("file_url")
    if isinstance(file_url, str) and file_url:
        return {"type": "input_file", "file_url": file_url}
    file_data = file_info.get("file_data")
    if not isinstance(file_data, str):
        file_data = file_info.get("data") if isinstance(file_info.get("data"), str) else None
    if isinstance(file_data, str):
        mime_type = file_info.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = file_info.get("content_type")
        if not isinstance(mime_type, str) or not mime_type:
            mime_type = "application/octet-stream"
        return {"type": "input_file", "file_url": f"data:{mime_type};base64,{file_data}"}
    return {"type": "input_file"}
