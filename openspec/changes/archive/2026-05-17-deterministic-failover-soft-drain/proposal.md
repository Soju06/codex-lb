# Deterministic Failover & Soft Drain

## Problem

When an upstream account hits a rate limit (HTTP 429) or quota exhaustion, codex-lb's handling is **inconsistent across transports** and often **leaks the error to the client** instead of transparently switching to another account:

- **SSE streaming**: First-event `response.failed` triggers account failover via `_RetryableStreamError`, but connect-phase `ProxyResponseError(429)` bypasses the retry loop entirely (`status_code != 500` → immediate raise).
- **Compact HTTP**: 429/quota errors are surfaced because `retryable_same_contract=False` for these codes.
- **WebSocket**: Relay architecture forwards upstream errors directly; no retry path exists at any phase.

Additionally, account selection is **reactive only** — it waits for a hard 429/quota failure before excluding an account. There is no mechanism to preemptively drain accounts approaching their limits.

## Solution

### 1. Canonical Error Classifier

Introduce a single `classify_upstream_failure()` function that normalises all upstream errors into `(failure_class, phase)` regardless of transport. This replaces the scattered `if code in {...}` branches across `_handle_stream_error`, `_stream_with_retry`, and `compact_responses`.

### 2. Deterministic Pre-commit Failover

Add a pure `failover_decision()` function with one invariant:

> **If the client has not received any downstream bytes/events/frames, failover to the next account. Otherwise, surface the error.**

This applies uniformly to all transports:
- **SSE streaming**: connect-phase 429 and first-event `response.failed` trigger failover before any `yield`.
- **Compact HTTP**: 429/quota triggers failover before response write.
- **WebSocket**: handshake-phase failure triggers failover before first frame relay.

### 3. Soft Drain State Machine

Add a `health_tier` field (0=HEALTHY, 1=DRAINING, 2=PROBING) to `RuntimeState` and `AccountState`:

- **DRAINING**: Entered when `primary_used ≥ 85%`, `secondary_used ≥ 90%`, or `≥ 2 transient errors in 60s`. New requests prefer other accounts; existing sticky sessions continue.
- **PROBING**: Entered after 60s quiet period in DRAINING. Limited traffic to verify recovery.
- **HEALTHY**: Restored after 3 consecutive successful probes.

Selection priority: `HEALTHY > PROBING > DRAINING`. All tiers remain selectable as fallback — drain never hard-blocks.

## Changes

### Phase 1 — Soft Drain
- Extend `RuntimeState` with `health_tier`, `drain_entered_at`, `probe_success_streak`.
- Extend `AccountState` with `health_tier`.
- Add `evaluate_health_tier()` pure function to `logic.py`.
- Modify `select_account()` to partition candidates by tier: `healthy or probing or draining`.
- Wire tier evaluation into `_build_states()` / `_state_from_account()`.
- Update `record_success()` / `record_error()` for probe streak tracking.
- Adjust sticky session logic to skip new sticky creation for DRAINING accounts.

### Phase 2 — Canonical Classifier
- Add `ClassifiedFailure`, `FailureClass`, `FailurePhase` types to `app/core/balancer/types.py`.
- Add `classify_upstream_failure()` to `app/modules/proxy/helpers.py`.
- Modify `_handle_stream_error()` to use classifier and return `ClassifiedFailure`.

### Phase 3 — Pre-commit Failover
- Add `failover_decision()` pure function to `logic.py`.
- Modify `_stream_with_retry`: catch connect-phase `ProxyResponseError(429)` → classify → failover.
- Modify `compact_responses`: catch 429/quota → classify → failover to next account.
- Add WebSocket handshake-phase failover before first frame relay.
- Add `DashboardSettings` feature flags: `soft_drain_enabled`, `deterministic_failover_enabled`.

### Phase 4 — Observability
- Structured failover decision logs with `request_id`, `transport`, `phase`, `failure_class`, `action`.
- Prometheus counters: `failover_total`, `drain_transitions_total`, `client_exposed_errors_total`.

## Impact

- Clients see fewer upstream rate-limit/quota errors — transparent failover handles them.
- Accounts approaching limits are proactively drained, reducing hard failure frequency.
- All existing routing strategies (`capacity_weighted`, `usage_weighted`, `round_robin`) continue to work; `health_tier` is an orthogonal filtering layer.
- Feature-flagged: both soft-drain and deterministic-failover can be enabled independently.
- No DB schema changes required (soft drain is runtime-only state).
