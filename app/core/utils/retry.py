from __future__ import annotations

import re

_RETRY_PATTERN = re.compile(r"(?i)try again in\s*(\d+(?:\.\d+)?)\s*(s|ms|seconds?)")


def parse_retry_after(message: str) -> float | None:
    match = _RETRY_PATTERN.search(message or "")
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "ms":
        return value / 1000
    return value
