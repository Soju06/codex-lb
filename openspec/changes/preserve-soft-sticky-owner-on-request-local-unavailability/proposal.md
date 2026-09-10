# Change: preserve-soft-sticky-owner-on-request-local-unavailability

## Why

A Codex conversation over HTTP re-runs account selection on every turn, while the same conversation over the WebSocket bridge selects once and keeps its upstream socket. Both derive the same soft, TTL-bounded `prompt_cache` thread row, so the difference is that the HTTP path re-evaluates that row under per-turn pressure. Today the row is well-behaved when its owner is *visible but unhealthy* (rate-limited, in error backoff): the fallback is request-local and the mapping is kept (`sticky-session-operations`, "Sticky sessions are explicitly typed"). But when the owner is *absent from the request's candidates* — because this request's own retry loop excluded it after one transient upstream failure (a code-less burst 429, a response-create cap rejection) or because the per-account stream-cap filter dropped it — selection treats the owner as removed and permanently rebinds the conversation to the fallback. Under load that moves the whole rest of the conversation to a cold account (13–40 %/h owner switches, ~3.4 accounts per conversation during the 09-08 storm) and defeats the prompt cache the row exists to protect.

## What Changes

- A soft TTL-bounded (`prompt_cache`) owner that is missing from the request's selectable candidates *only* because of request-local pressure keeps its mapping: the alternate serves this request, no delete or upsert is written, and the existing `internal_soft_affinity_spillover` diagnostic is emitted. "Request-local pressure" is exactly two conditions: the owner is present in the routable pool but filtered by the per-account concurrency caps, or the owner is in the request's excluded set (its retry loop already failed over from it) while it remains in the request's continuity-owner scope with a recoverable persisted status (`active`, `reauth_required`, `rate_limited`, `quota_exceeded`).
- The existing `internal_soft_affinity_spillover` diagnostic now honours the request's sensitive-detail redaction flag (both account ids print as `<redacted>` when asked), because this change makes it fire on the common thread-header path and for `codex_control` callers that request redaction, rather than only on the rarely enabled bare-session cap spill.
- Permanent removal is unchanged: an owner that is overload-isolated, explicitly reallocated (`reallocate_sticky`), `paused`/`deactivated`, or outside the request's continuity-owner scope (deleted, out of API-key scope, not security-work authorized) is still rebound to the fallback. Hard rows (`codex_session` turn-state, legacy raw keys) and durable no-TTL bare `codex_session` rows keep their existing rules.
- `StickySelectionRequest` carries the request's excluded account ids (`exclude_account_ids`, defaulted) so the sticky predicate can tell "excluded by this request" from "not in the pool"; `LoadBalancer.select_account` forwards the set it already receives from the retry loop. No new setting: the behaviour is the same rule the bare process-session cap spillover already applies, generalised to the TTL-bounded soft kind, and the settings ratchet is full (130/130).

Cross-references (no MODIFIED blocks): "Bare process-session cap spillover is non-mutating" (the rule being generalised), "Unusable account transitions remove persistent affinity bindings" (mapping deletion on the status transition itself stays the permanent-removal path), "Isolated accounts release their soft sticky owners" (an isolated owner is still rebound).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: ADDED requirement "Request-local unavailability of a soft TTL-bounded owner is non-mutating".

## Impact

- Code: `app/modules/proxy/_load_balancer/sticky_selection.py` (one defaulted request field, one guarded predicate block, one defaulted `redact_sensitive_details` kwarg on `_select_with_stickiness` used by the spillover log), `app/modules/proxy/load_balancer.py` (one pass-through line in `select_account` plus the kwarg forward in the `_select_with_stickiness` wrapper; 2982/3021, `select_account` span within its ceiling). `service.py`, `http_bridge/mixin.py`, `streaming/mixin.py`, `retry.py` untouched.
- Data: one fewer sticky upsert per spill; no schema change.
- Operators: no action, no new `CODEX_LB_*` setting. A conversation whose owner keeps failing is served by a request-local fallback each turn until the owner enters error backoff (same request-local spill), is isolated (rebind), or the 1800 s TTL expires — the behaviour already chosen for the visible-but-unhealthy case. Monitor the `internal_soft_affinity_spillover` rate after deploy.
