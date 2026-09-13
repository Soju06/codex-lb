"""Responses body projection for OpenAI-compatible model sources (#2123 WP-C1).

Two independent, pure concerns (design v3 §4.4, §4.6; CP-7):

* ``strip_source_telemetry`` removes the Codex client telemetry a source must
  never see -- whole fields where the field itself is Codex-only, a single key
  inside the standard ``stream_options`` object -- and forwards everything else
  verbatim: direct source routing never fails closed on an unknown field.
* ``overflow_portability_view`` builds the positive-allowlist view the overflow
  decision (WP-C2) classifies. Anything outside the allowlist declines with a
  closed reason instead of being forwarded; the gpt-5.6 Responses-Lite bundle
  (``reasoning.context``, ``additional_tools`` items) is never portable.

The view is built from the *stripped* body (``strip_source_telemetry`` runs
first on every source dispatch), so telemetry fields never reach the allowlist.
The constants are the single definition of the field sets; ``replay_safety``
evaluates the verdict on the view and never imports anything else from here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from app.core.openai.requests import responses_input_uses_lite_tools
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping

type MutableJsonObject = dict[str, JsonValue]

# Codex client telemetry that an OpenAI-compatible source rejects or must not see.
# Whole top-level fields, because neither is a Responses API field:
# ``client_metadata`` (installation/session/thread/turn ids and turn metadata)
# and ``access_programs``.
STRIPPED_TELEMETRY_FIELDS: frozenset[str] = frozenset({"client_metadata", "access_programs"})

# ``stream_options`` is a standard Responses field (``include_obfuscation``)
# that Codex extends with ``reasoning_summary_delivery`` (its reasoning-summary
# delivery mode), so only that key is telemetry: it is removed from the object
# and the object is dropped once the removal leaves it empty -- the shape every
# Codex body has -- while an SDK client's ``include_obfuscation`` is forwarded
# unchanged, as ``main`` forwarded it.
STREAM_OPTIONS_FIELD = "stream_options"
STRIPPED_STREAM_OPTIONS_KEYS: frozenset[str] = frozenset({"reasoning_summary_delivery"})

# Stripped only for overflow dispatch (``strip_service_tier=True``): source
# pricing has no tier dimension and the source reservation settles with
# ``service_tier=None``; direct routing keeps forwarding the client's tier.
SERVICE_TIER_FIELD = "service_tier"

# Top-level Responses fields the portability view admits (design §4.6). Every
# other field declines the overflow decision; ``prompt_cache_key`` is forwarded
# verbatim and ``reasoning`` may only carry ``effort``/``summary``.
# ``previous_response_id``, ``conversation`` and ``prompt`` are admitted so the
# verdict declines them as history rather than as unknown fields.
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

# ``reasoning.context`` (``all_turns``) is Responses-Lite only; any key beyond
# these two marks the body as a Lite body.
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

# Closed set of the reasons above (metric labels, tests); derived so it cannot drift.
DECLINE_REASONS: frozenset[str] = frozenset(get_args(DeclineReason))


@dataclass(frozen=True, slots=True)
class PortabilityView:
    """Allowlisted Responses body; ``reasoning`` carries ``effort``/``summary`` only.

    A shallow copy of the stripped body: later shaping steps on the source body
    (tool dropping, reasoning restoration) must not alter the evidence the
    overflow decision was made on.
    """

    body: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Declined:
    reason: DeclineReason
    # Offending field name(s) / item type for the operator WARN; never client-visible.
    detail: str | None = None


def strip_source_telemetry(payload: MutableJsonObject, *, strip_service_tier: bool = False) -> MutableJsonObject:
    """Remove exactly the Codex telemetry (+ ``service_tier`` when flagged) in place; returns ``payload``.

    ``STRIPPED_TELEMETRY_FIELDS`` are removed whole; from a ``stream_options``
    object only ``STRIPPED_STREAM_OPTIONS_KEYS`` are removed, and the object
    itself is dropped when that removal leaves nothing behind. Everything else
    -- ``prompt_cache_key``, ``tools``, ``include``, ``max_output_tokens``,
    ``prompt_cache_retention``, ``background``, ``max_tool_calls``, a
    ``stream_options.include_obfuscation``, a ``stream_options`` that is not an
    object, and any field this proxy has never seen -- is forwarded untouched,
    so direct source routing never fails closed on an unknown field.
    """

    for field in STRIPPED_TELEMETRY_FIELDS:
        payload.pop(field, None)
    stream_options = payload.get(STREAM_OPTIONS_FIELD)
    if isinstance(stream_options, dict):
        removed = False
        for key in STRIPPED_STREAM_OPTIONS_KEYS:
            if key in stream_options:
                del stream_options[key]
                removed = True
        if removed and not stream_options:
            del payload[STREAM_OPTIONS_FIELD]
    if strip_service_tier:
        payload.pop(SERVICE_TIER_FIELD, None)
    return payload


def overflow_portability_view(source_body: Mapping[str, JsonValue]) -> PortabilityView | Declined:
    """Project ``source_body`` onto ``OVERFLOW_VIEW_FIELDS`` or decline with a closed reason; never raises.

    Order: unknown top-level field -> ``not_portable_unknown_field`` (every
    offending name, sorted, in ``detail``); ``reasoning`` outside
    ``effort``/``summary`` (Lite ``context``) or an ``additional_tools`` input
    item (the Lite tool bundle) -> ``not_portable_lite_namespace``. A body
    without an ``input`` list, or with a non-object ``reasoning``, is outside
    the shapes the allowlist describes and is declined, never raised on.
    """

    unknown_fields = sorted(field for field in source_body if field not in OVERFLOW_VIEW_FIELDS)
    if unknown_fields:
        return Declined("not_portable_unknown_field", ",".join(unknown_fields))
    reasoning = source_body.get("reasoning")
    if reasoning is not None:
        if not is_json_mapping(reasoning):
            return Declined("not_portable_unknown_field", "reasoning")
        lite_keys = sorted(key for key in reasoning if key not in OVERFLOW_VIEW_REASONING_FIELDS)
        if lite_keys:
            return Declined("not_portable_lite_namespace", ",".join(f"reasoning.{key}" for key in lite_keys))
    if responses_input_uses_lite_tools(source_body.get("input")):
        return Declined("not_portable_lite_namespace", "additional_tools")
    return PortabilityView(body=dict(source_body))
