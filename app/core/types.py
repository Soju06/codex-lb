from __future__ import annotations

type JsonValue = bool | int | float | str | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
