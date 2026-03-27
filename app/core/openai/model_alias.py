"""Parse model alias strings that embed provider prefixes and reasoning effort.

Examples::

    openai/gpt-5.4(high) → ("gpt-5.4", "high")
    openai/gpt-5.4       → ("gpt-5.4", None)
    gpt-5.4(medium)      → ("gpt-5.4", "medium")
    gpt-5.4              → ("gpt-5.4", None)
"""

from __future__ import annotations

import re

_VALID_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})

_MODEL_EFFORT_RE = re.compile(
    r"^(?P<model>.+?)\((?P<effort>[A-Za-z]+)\)$",
)


def parse_model_alias(raw_model: str) -> tuple[str, str | None]:
    """Return ``(canonical_model, reasoning_effort | None)``."""
    name = raw_model

    # Strip provider prefix  (e.g. "openai/gpt-5.4" → "gpt-5.4")
    slash_idx = name.find("/")
    if slash_idx != -1:
        name = name[slash_idx + 1 :]

    # Extract reasoning effort suffix  (e.g. "gpt-5.4(high)" → "gpt-5.4", "high")
    effort: str | None = None
    match = _MODEL_EFFORT_RE.match(name)
    if match:
        candidate = match.group("effort").lower()
        if candidate in _VALID_EFFORTS:
            name = match.group("model")
            effort = candidate

    return name, effort
