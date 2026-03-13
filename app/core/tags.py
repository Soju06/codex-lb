from __future__ import annotations

from collections.abc import Iterable


def normalize_tags(values: Iterable[str] | None) -> list[str]:
    if values is None:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        candidate = raw_value.strip().lower()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized
