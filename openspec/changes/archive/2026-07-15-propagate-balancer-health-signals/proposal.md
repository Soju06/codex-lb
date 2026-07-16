# Propagate Balancer Health Signals Across Replicas

## Why

In multi-replica deployments the balancer's rate-limit cooldown is replica-local, so a 429 without upstream reset metadata is undone within seconds: the marking replica persists `status=RATE_LIMITED` and `blocked_at` but keeps the cooldown only in process memory (`RuntimeState.cooldown_until`). Any peer's next selection pass computes `runtime_reset=None`, flips the row back to `ACTIVE` via `apply_usage_quota` (usage below 100%), CAS-writes it, and re-routes traffic to the still-throttled account. The DB row flaps `RATE_LIMITED -> ACTIVE -> RATE_LIMITED` for the throttle's duration and every replica except the marking one keeps hammering the throttled account, burning failover budget fleet-wide. Retry-After hints are equally invisible to peers. This change makes rate-limit cooldowns durable cross-replica using the existing `reset_at`/`blocked_at` columns (no migration) and normatively documents which soft health signals are per-replica by design.

## What Changes

- `handle_rate_limit` (app/core/balancer/logic.py) persists the resolved cooldown deadline into `state.reset_at` when the 429 carries no `resets_at`/`resets_in_seconds` metadata: the Retry-After hint duration verbatim, or the error-count backoff floored at a new `RATE_LIMITED_MIN_COOLDOWN_SECONDS` (30s) constant. The write rides the existing `mark_rate_limit -> _persist_state` status update; the local `cooldown_until` keeps the raw backoff so the marking replica's early-recovery gates are unchanged.
- `_state_from_account` (app/modules/proxy/load_balancer.py) synthesizes a peer-side floor for legacy/incomplete rows: `RATE_LIMITED` rows with `blocked_at` set but `reset_at` NULL are held out of rotation until `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`; once the floor elapses, recovery proceeds through the existing CAS-guarded persistence path (`update_status_if_current`).
- Spec deltas (account-routing): the Retry-After requirement now mandates persisting the resolved deadline; a new requirement forbids peer replicas from flipping a cooling account back to `ACTIVE` before the persisted deadline; a new requirement declares transient error counts, error backoff, drain/probe health tiers, and in-flight pressure replica-local advisory state.
- Two-replica regression tests at the selection product path (two `LoadBalancer` instances over one database).

Out of scope (follow-up changes, see design.md): replica-decorrelated round-robin tie-breaking and stateless staleness-first usage-refresh account selection.
