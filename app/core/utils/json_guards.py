from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard

from app.core.types import JsonValue


def is_json_mapping(value: object) -> TypeGuard[Mapping[str, JsonValue]]:
    return isinstance(value, Mapping)


def is_json_dict(value: object) -> TypeGuard[dict[str, JsonValue]]:
    return isinstance(value, dict)


def is_json_list(value: object) -> TypeGuard[list[JsonValue]]:
    return isinstance(value, list)
