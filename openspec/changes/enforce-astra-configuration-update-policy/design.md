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
`request_policy` (cycle).

Fingerprint HTTP-bridge input from `payload.input` after Astra
preparation mutates that list, so Astra prepends are counted without a
global rewrite of non-Astra requests.

For anchored HTTP full resends, validate a trimmed copy before admission,
but retain the original payload for the bridge trim detector. Its existing
count and fingerprint override must describe client history, not the
delta plus injected reset. Non-bridge forwarding keeps the validated copy.
Bridge preparation also uses a copy so a late injected reset cannot shift
the client prefix before the subsequent stored-context comparison.

Rejected: landing the full #2089 branch. Maintainer required a split.
