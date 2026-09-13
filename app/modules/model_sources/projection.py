"""Responses body projection for OpenAI-compatible model sources.

``strip_source_telemetry`` removes the Codex client telemetry a source must
never see -- whole fields where the field itself is Codex-only, a single key
inside the standard ``stream_options`` object -- and forwards everything else
verbatim: direct source routing never fails closed on an unknown field.
"""

from __future__ import annotations

from app.core.types import JsonValue

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
# unchanged.
STREAM_OPTIONS_FIELD = "stream_options"
STRIPPED_STREAM_OPTIONS_KEYS: frozenset[str] = frozenset({"reasoning_summary_delivery"})


def strip_source_telemetry(payload: MutableJsonObject) -> MutableJsonObject:
    """Remove exactly the Codex telemetry in place; returns ``payload``.

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
    return payload
