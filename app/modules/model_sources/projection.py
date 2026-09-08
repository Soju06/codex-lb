"""Responses body projection for OpenAI-compatible model sources (#2123 WP-C1).

Interfaces contract only (design v3 §4.4, §4.6). Two independent concerns:

* ``strip_source_telemetry`` removes the Codex client telemetry a source must
  never see and forwards everything else verbatim -- direct source routing
  never fails closed on an unknown field.
* ``overflow_portability_view`` builds the positive-allowlist view the overflow
  decision (WP-C2) classifies. Anything outside the allowlist declines with a
  closed reason instead of being forwarded; the gpt-5.6 Responses-Lite bundle
  (``reasoning.context``, ``additional_tools`` items) is never portable.

Bodies are filled in by the projection-gate package; the constants are the
single definition of the field sets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.core.types import JsonValue

type MutableJsonObject = dict[str, JsonValue]

# Codex client telemetry that an OpenAI-compatible source rejects or must not see.
STRIPPED_TELEMETRY_FIELDS: frozenset[str] = frozenset({"client_metadata", "stream_options", "access_programs"})

# Top-level Responses fields the portability view admits (design §4.6). Every
# other field declines the overflow decision; ``prompt_cache_key`` is forwarded
# verbatim and ``reasoning`` is reduced to ``effort``/``summary``.
OVERFLOW_VIEW_FIELDS: frozenset[str] = frozenset(
    {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "text",
        "include",
        "store",
        "stream",
        "truncation",
        "max_output_tokens",
        "temperature",
        "top_p",
        "metadata",
        "user",
        "safety_identifier",
        "prompt_cache_key",
        "prompt_cache_retention",
        "previous_response_id",
        "conversation",
        "prompt",
    }
)

OVERFLOW_VIEW_REASONING_FIELDS: frozenset[str] = frozenset({"effort", "summary"})

DeclineReason = Literal[
    "not_portable_history",
    "not_portable_lite_namespace",
    "not_portable_tools",
    "not_portable_items",
    "not_portable_vision",
    "not_portable_unknown_field",
    "turn_state_bound",
]


@dataclass(frozen=True, slots=True)
class PortabilityView:
    """Allowlisted Responses body; ``reasoning`` reduced to ``effort``/``summary``."""

    body: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Declined:
    reason: DeclineReason
    detail: str | None = None


def strip_source_telemetry(payload: MutableJsonObject, *, strip_service_tier: bool = False) -> MutableJsonObject:
    """Remove exactly ``STRIPPED_TELEMETRY_FIELDS`` (+ ``service_tier`` when flagged) in place; returns ``payload``."""

    raise NotImplementedError


def overflow_portability_view(source_body: Mapping[str, JsonValue]) -> PortabilityView | Declined:
    """Project ``source_body`` onto ``OVERFLOW_VIEW_FIELDS`` or decline with a closed reason; never raises."""

    raise NotImplementedError
