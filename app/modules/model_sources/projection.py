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
* ``neutralize_overflow_egress`` rewrites the handful of request fields that a
  ``portable`` verdict would otherwise forward to a *third-party* provider
  verbatim -- the client's prompt-cache namespace, its end-user identifiers, and
  the request for encrypted reasoning. Overflow only; direct source routing is
  untouched.

The view is built from the *stripped* body (``strip_source_telemetry`` runs
first on every source dispatch), so telemetry fields never reach the allowlist.
The constants are the single definition of the field sets; ``replay_safety``
evaluates the verdict on the view and never imports anything else from here.
"""

from __future__ import annotations

import hashlib
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
# other field declines the overflow decision, and ``reasoning`` may only carry
# ``effort``/``summary``. Admitting a *name* here is not agreement that its
# *value* may be handed to a third-party source: the value side is closed by
# ``replay_safety.OVERFLOW_FIELD_CLASSIFICATION`` and, for four of these fields,
# by ``neutralize_overflow_egress`` below.
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

# -- Overflow egress neutralisation (#2123) ------------------------------------
#
# Admitting a field into the view is not the same as agreeing that its *value*
# may be handed to a third-party provider. These are the fields where the value
# is the problem, and where declining would be worse than rewriting.
#
# ``include`` entries the egress removes instead of declining on. ``include``
# asks the source for extra *response* artifacts, so dropping an entry can only
# subtract from what comes back -- it never changes the generation. The one
# entry here is on 100 % of captured Codex 0.154.0 bodies (60/60 in the
# 2026-09-13 capture sweep), so declining on it would make overflow unreachable
# for every real Codex request; and the artifact it asks the destination to mint
# is the very thing ``_mapping_has_account_scoped_reference`` and
# ``_HISTORY_ITEM_TYPES`` reject in every other position. Removing it reaches the
# end state the predicate already wants -- no encrypted reasoning minted at the
# source, so the client's next turn stays portable -- at no cost to this turn.
NEUTRALIZED_INCLUDE_VALUES: frozenset[str] = frozenset({"reasoning.encrypted_content"})

# Client-chosen identifiers replaced with a proxy-minted opaque token, mapped to
# the token domain they share. ``prompt_cache_key`` is a *namespace* on the
# destination: a value one tenant chooses and another tenant can guess collides
# in a prompt cache this proxy has measured to be shared rather than isolated
# (2026-09-11 cross-account cache probe). ``user``/``safety_identifier`` are
# end-user identifiers by the OpenAI specification -- in practice emails -- and a
# stable opaque token is exactly what the vendor documents sending instead.
# Both keep their function: the token is a pure function of the value and the
# namespace, so the same tenant re-sending the same key hits the same cache and
# the same end user stays one distinguishable subject.
NEUTRALIZED_IDENTIFIER_FIELDS: Mapping[str, str] = {
    "prompt_cache_key": "prompt_cache",
    "safety_identifier": "end_user",
    "user": "end_user",
}

# The wire marker of a proxy-minted value. ``replay_safety`` recognises it by
# shape, so the verdict can require the rewrite to have happened without being
# handed the namespace.
OPAQUE_VALUE_PREFIX = "codexlb-"
OPAQUE_VALUE_DIGEST_CHARS = 32

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
    so direct source routing never fails closed on an unknown field. Subscription
    overflow runs ``neutralize_overflow_egress`` on top of this step; direct
    source routing does not, and is unchanged.
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


def overflow_opaque_value(value: str, *, namespace: str | None, domain: str) -> str:
    """A proxy-minted stand-in for a client-chosen identifier: ``codexlb-`` plus 32 hex characters.

    Deterministic in ``(namespace, domain, value)`` and in nothing else, so two
    requests from the same tenant carrying the same identifier produce the same
    token -- the property ``prompt_cache_key`` exists for -- while two tenants
    that chose the same identifier never do. ``domain`` keeps the cache
    namespace and the end-user identifier in separate token spaces, so a cache
    key that happens to equal an end-user id does not alias it.
    """

    material = "\x00".join((namespace or "", domain, value)).encode("utf-8")
    return f"{OPAQUE_VALUE_PREFIX}{hashlib.sha256(material).hexdigest()[:OPAQUE_VALUE_DIGEST_CHARS]}"


def neutralize_overflow_egress(payload: MutableJsonObject, *, namespace: str | None) -> MutableJsonObject:
    """Rewrite the overflow-only fields in place before the body leaves for a source; returns ``payload``.

    ``namespace`` is the tenant the rewritten identifiers are scoped to (the API
    key id, ``None`` when the request carried no key). Two changes and no
    others: ``NEUTRALIZED_INCLUDE_VALUES`` are removed from ``include`` (and
    ``include`` itself is dropped once that empties it, the way
    ``strip_source_telemetry`` drops an emptied ``stream_options``), and each
    ``NEUTRALIZED_IDENTIFIER_FIELDS`` string becomes ``overflow_opaque_value``.
    A non-string in either slot is left exactly as it is for the verdict to
    decline -- this projection never makes an unclassifiable value look
    classified. Direct source routing never calls this: it forwards what the
    client sent, and it is not the direction this guards.
    """

    include = payload.get("include")
    if isinstance(include, list):
        kept = [entry for entry in include if not (isinstance(entry, str) and entry in NEUTRALIZED_INCLUDE_VALUES)]
        if len(kept) != len(include):
            if kept:
                payload["include"] = kept
            else:
                del payload["include"]
    for field, domain in NEUTRALIZED_IDENTIFIER_FIELDS.items():
        value = payload.get(field)
        if isinstance(value, str):
            payload[field] = overflow_opaque_value(value, namespace=namespace, domain=domain)
    return payload
