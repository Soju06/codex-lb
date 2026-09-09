## Context

The failing product path is an API-key-scoped `/backend-api/codex/responses` request whose durable Codex session owner is outside the key assignment scope. Selection correctly returns `hard_affinity_saturated`, but the recovery loop retries until its 75-second request budget expires. The same behavior is reproducible on the beta.4 source baseline and is therefore a separate follow-up rather than a beta.5 merge change.

## Decisions

1. Preserve ownership fail-closed behavior. The owner must never be retired, rebound, or replaced merely because selection reached its deadline.
2. Detect a permanent scope mismatch in sticky selection using the authenticated policy pool captured before health, model, exclusions, and concurrency filters. Carry that fact in the typed selection result. An owner merely in cooldown, quota exhaustion, or excluded for a retry remains recoverable when it is still in scope.
3. Make the shared selection-recovery helper decline waits for the typed scope mismatch. Existing SSE, bridge, and WebSocket terminal paths retain their current envelope mappings and cleanup. No request-global error cache or change to generic timeout translation is needed.
4. Use the existing virtual `Clock`/`Scheduler` seams to prove that scope mismatches schedule no timers or progress heartbeats even near a deadline. Strengthen the public route regression with single-selection, exact envelope, unchanged durable owner and empty lease assertions.

## Failure modes and constraints

- A hard owner outside an API-key assignment scope remains fail-closed before upstream dispatch.
- A selector that times out before resolving any owner still follows the existing timeout path; this change does not infer ownership from a stale earlier failure.
- Soft affinity and eligible capacity waits retain their current retry behavior.
- A real upstream connect or stream attempt that consumes the request budget still returns `upstream_request_timeout`.
- No production database, HA runtime state, or deployment artifact is changed by this follow-up.

## Verification

Run focused streaming/bridge/WebSocket regressions, the full sticky-session module, unit/simulation suites, lint/type/timing checks, and strict OpenSpec validation before archive.
