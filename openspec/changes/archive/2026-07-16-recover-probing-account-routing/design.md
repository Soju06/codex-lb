## Context

Soft-drain health is replica-local. After two recent transient errors an account drains, then becomes `PROBING` after the fixed 60-second quiet period. The health-tier-aware selection ladder currently evaluates `healthy or probing or draining`; therefore any healthy account permanently masks every probing account. Probe successes are only recorded after ordinary proxied requests, while the operator Force Probe bypasses the proxy service and never reaches this state machine.

## Goals / Non-Goals

**Goals:**

- Guarantee bounded opportunities for a probing account to receive real traffic even while healthy accounts remain.
- Keep existing sticky owners stable and preserve all eligibility, cooldown, quota, routing-security, and account-cap gates.
- Let a successful operator Force Probe count toward the same process-local recovery streak.
- Add no operator setting or persistent health state.

**Non-Goals:**

- Persist or coordinate health tiers across replicas.
- Treat an HTTP error from Force Probe as a new persistent account failure classification.
- Change the fixed drain/probe thresholds, upstream error taxonomy, or retry commit boundary.
- Guarantee recovery for an account that continues to fail or remains usage-drained.

## Decisions

### Use `last_selected_at` as the bounded recovery clock

For health-tier-aware routing strategies, selection will consider one probing account "due" when it has never been selected or its last selection is at least `PROBE_QUIET_SECONDS` old. A recovery-only availability pass runs before budget and `burn_first`/`normal`/`preserve` preference shortcuts. The oldest eligible due account, with account id as a stable tie-break, becomes the effective health pool for that selection. Otherwise healthy-first behavior is unchanged. Strategies that intentionally bypass health-tier ordering remain unchanged.

This reuses replica-local state already updated atomically at selection and avoids a second timestamp or scheduler. Sticky selection reads any existing owner before entering the runtime lock, then temporarily reserves the oldest due probe's timestamp and captures the current runtime version under that lock before sticky database work. The reservation covers both unbound selection and fallback from an unavailable owner. Its timestamp and version form a CAS token checked before final lease admission and again after selection-state persistence. A mismatch restores the timestamp and retries from newer runtime state. Sticky selection returns one provisional desired-state mutation instead of writing immediately; the caller applies it only after cap classification, lease admission, state persistence, and the probe CAS. Successful reallocation becomes one atomic upsert rather than delete then upsert, and failed admission needs no compensating cross-replica write. Every earlier exit restores the prior timestamp. Reserve/release does not advance the runtime health-observation version, so a concurrent accepted Force Probe is invalidated only by a committed selection or actual health mutation. Concurrent sticky requests therefore cannot all admit the same due probe. Selecting only one candidate bounds each selection decision and rotates multiple probing accounts fairly. A separate random sampling percentage was rejected because low traffic could still starve indefinitely and it would add configuration pressure.

### Preserve sticky owners before recovery admission

`_select_with_stickiness` already attempts a selectable pinned owner as a singleton before choosing from the wider pool. Recovery admission remains inside wider pool selection, so it applies to unbound or fallback work and cannot evict an eligible existing owner merely to run a probe.

For hard-sticky ownership, cap filtering retains only the pinned owner as a fail-closed exception. Every fallback still passes the local account-cap filter before recovery preference runs, preventing a due but saturated probing account from receiving the fallback mapping and failing at lease acquisition. Because the owner exception keeps the selection pool non-empty, fallback cap exhaustion is tracked separately by comparing non-cap availability across defensive copies of the complete fallback pool before and after cap filtering. Keeping candidates together matters for opportunistic traffic, whose expendability rule depends on other foreground capacity. An under-cap but rate-limited, quota-exceeded, paused, cooling-down, or transient-backoff account therefore cannot hide or bypass the fact that every normally usable fallback is saturated. Once established, the local cap reason overrides any non-owner backoff result and discards provisional sticky mutation, preserving fail-closed ownership.

### Count accepted Force Probes in the replica-local state machine

After the direct probe and forced usage refresh complete, the accounts API reports the account id and HTTP result to the process-local proxy service. For a 2xx response, the load balancer captures the current runtime version, reloads the refreshed primary, secondary, and monthly rows, and passes them through the same elapsed-window, weekly-only, zero-primary-capacity, and plan-applicable monthly normalization used by ordinary selection. Settlement proceeds only if the runtime version is unchanged, so a failure recorded while repository reads are in flight wins over the older probe result. An accepted success clears transient error state, advances a probing streak, and applies the same fixed health transition rules using those normalized values. A non-2xx response or network sentinel resets an in-progress probing streak but does not invent a transient error or override persistent quota/status handling.

The API orchestration boundary is used because it already owns the application request and can reach the singleton proxy service without coupling the accounts service to proxy internals. The external response schema remains unchanged.

### Centralize runtime tier transitions

The existing usage-window normalization, zero-primary-capacity health interpretation, and tier/timestamp mutation in `_state_from_account` are extracted into helpers reused by Force Probe settlement. This keeps plan capacity, window semantics, `drain_entered_at`, `probe_success_streak`, the fixed thresholds, and version changes consistent rather than duplicating a second recovery state machine.

## Risks / Trade-offs

- **A recovering account receives a real request before three successes are known** → admission is limited to one due account per quiet interval, all normal eligibility/cap gates remain active, and existing pre-visible retry behavior can move off a failed account.
- **A selected request never reaches upstream** → `last_selected_at` still postpones the next probe by one quiet interval; this is conservative and bounded, not permanent starvation.
- **HTTP 2xx precedes a later SSE error** → Force Probe intentionally consumes only response acceptance; requiring full stream completion would change its latency and established contract. Three accepted probes are still required, while ordinary proxied failures can drain the account again.
- **Replica-local results differ across processes** → this is consistent with the existing account-routing contract; each replica rehabilitates from its own observations.

## Migration Plan

No schema or configuration migration is required. Deploying a new replica starts with empty advisory health state as today. Rollback restores healthy-first starvation behavior without changing persisted account rows.

## Open Questions

None.
