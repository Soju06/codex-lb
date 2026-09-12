## Context

#2089 bundled configuration-update policy with async tools and WebSocket
steering. Soju06 asked to split; this change is slice (a). The reference
client already writes `configuration_update` items. It does not emit
`response.steer` or `async: true` tools.

## Goals / Non-Goals

**Goals:**

- Close the API-key bypass through in-input `configuration_update`.
- Preserve request-level cache prefix and input order.
- Map Ultra to Max only at subscription serialization.

**Non-Goals:**

- Adding `gpt-6-astra` to the bootstrap catalog (#2085).
- Moving wire-effort aliases out of `request_policy.py` (#2085 conflict).
- Async tool continuity and WebSocket steering.
- Changing `payload.input` fingerprinting to `upstream_payload["input"]`
  for all models.

## Decisions

Keep `_REASONING_EFFORT_WIRE_ALIASES` and `resolve_wire_reasoning_effort`
in `request_policy.py`. `requests.py` maps Ultra on configuration-update
items at `to_payload` with the same Ultra→Max rule, without importing
`request_policy` (cycle). Do not keep a local Astra effort enumeration:
`_astra_wire_effort` checks string type and aliases Ultra→Max, then
forwards the value. `disabled` and `none` are real wire values from the
reference client and MUST be accepted locally.

Prepend a leading `configuration_update` only when the key has
`enforced_reasoning_effort`. Allowed-list keys rely on per-request
validation. An omitted request-level effort on an allowed-list
continuation MUST match the fresh-request path: do not synthesize
`medium` and do not 403 from that fake default.

Fingerprint HTTP-bridge client history separately from the prepared wire
payload. When Astra continuation preparation inserts a reset, retain the
original input count and fingerprint even when no history trim is needed.
The wire payload and usage estimate include the reset; client-prefix metadata
does not. Do not globally rewrite non-Astra fingerprinting.

For anchored HTTP full resends of `gpt-6-astra`, validate a trimmed copy
before admission, but retain the original payload for the bridge trim
detector. Its existing count and fingerprint override must describe
client history, not the delta plus injected reset. Non-bridge forwarding
keeps the validated Astra copy. Non-Astra direct HTTP bodies MUST remain
untrimmed; `_prepare_http_fallback_payload` uses the same model gate.
HTTP-bridge internal trim may stay. Bridge preparation also uses a copy
so a late injected reset cannot shift the client prefix before the
subsequent stored-context comparison.

Rejected: landing the full #2089 branch. Maintainer required a split.

For a WebSocket Astra source candidate, resolve previous-response ownership
after continuity anchor injection and before choosing its schema or reserving
usage. Carry a resolved subscription owner into routing; do not reclassify it
as source-owned at connect time. Keep ordinary subscription preparation and
original-history bookkeeping unchanged. This matches HTTP owner precedence
without changing model-source routing or validating reconstructed wire values.
If anchor injection preserves a complete fresh-replay body, validate both the
selected suffix and that exact preserved body before reservation; a later
stale-anchor retry must not widen the admitted subscription request.
