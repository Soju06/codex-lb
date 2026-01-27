from __future__ import annotations

from typing import cast

from app.core.types import JsonValue


def coerce_messages(existing_instructions: str, messages: list[JsonValue]) -> tuple[str, list[JsonValue]]:
    instruction_parts: list[str] = []
    input_messages: list[JsonValue] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Each message must be an object.")
        message_dict = cast(dict[str, JsonValue], message)
        role_value = message_dict.get("role")
        role = role_value if isinstance(role_value, str) else None
        if role in ("system", "developer"):
            content_text = _content_to_text(message_dict.get("content"))
            if content_text:
                instruction_parts.append(content_text)
            continue
        input_messages.append(cast(JsonValue, message_dict))
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


def _content_to_text(content: object) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                part_dict = cast(dict[str, JsonValue], part)
                text = part_dict.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join([part for part in parts if part])
    if isinstance(content, dict):
        content_dict = cast(dict[str, JsonValue], content)
        text = content_dict.get("text")
        if isinstance(text, str):
            return text
        return None
    return None
