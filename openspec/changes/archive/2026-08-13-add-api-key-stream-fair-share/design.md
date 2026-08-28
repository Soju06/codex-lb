# Design: add-api-key-stream-fair-share

## Context

Per-account stream caps (`proxy_account_stream_limit`, replica-partitioned by `cap_partitioning.py`) are enforced against in-process `RuntimeState` counters under `LoadBalancer._runtime_lock`. Every stream lease is acquired inside one of two selection paths (`_load_balancer/sticky_selection.py`, `_load_balancer/unbound_selection.py`); the requesting `ApiKeyData` reaches `ProxyService._select_account_with_budget` but is consumed there — the balancer never sees key identity, and nothing tracks in-flight work per key. Local capacity denials (`account_stream_cap`) already have complete downstream semantics: `_LOCAL_ACCOUNT_CAP_ERROR_CODES` drives the transport park-and-retry loop (30s chunks, `waiting_for_account_capacity` keepalives, bounded by the per-request budget) and `LOCAL_OVERLOAD_CODES` drives 429 + `Retry-After` + `rate_limit_error` at the edge. Each park iteration re-runs selection from scratch, so any admission predicate evaluated during selection is automatically re-evaluated with fresh state.

## Goals

- Work-conserving: below the congestion threshold, zero admission change.
- Under congestion, max-min fairness across keys on stream slots; light keys starvation-proof; over-share keys cannot take freed slots until under share.
- Zero DB I/O and zero new locks on the admission hot path; no new transport code.
- Default off; one setting; stable low-cardinality denial reason.

## Non-goals

Exact global fairness across replicas/workers; response-create leases; opportunistic edge pre-check integration; token-weighted shares (all listed as follow-ups in D9).

## Decisions

### D1: Congestion-triggered max-min fair share, integer math

Gate activates only when `threshold_pct > 0` and `T*100 >= C*threshold_pct` (integer comparison, no floats). While congested a key admits iff `key_inflight + 1 <= max(2, C // active_keys)`. `active_keys` counts keys holding at least one in-flight stream lease on the candidate accounts, with the requester counted exactly once whether or not it is already active. Work-conserving by construction: an idle pool admits any burst; fairness pressure appears exactly when slots become scarce. Alternatives rejected: static per-key caps (waste idle capacity — explicit owner requirement), weighted queues (new mechanism, hot-path complexity), utilization-proportional probabilistic shedding (non-deterministic, hard to test/explain).

### D2: Candidate-set scoping for C, T, key counts

All four quantities are computed over the selection's candidate account set (post account-scope filtering). Account-scoped keys are therefore measured against the pool they can actually use; `required_account_id` attempts (preferred-account, single-account routing) see that account's cap as their pool, and the preferred-account attempt falls through to general selection on denial exactly as it does for cap exhaustion. An unscoped key's footprint is its full footprint; a scoped key's footprint is its footprint on scoped accounts — each is the right numerator for its own denominator.

### D3: Per-key counts live on `RuntimeState`, attribution on `AccountLease`

`AccountLease.api_key_id` attributes each stream lease; `RuntimeState.stream_key_inflight` (lazy dict, entries deleted at zero) holds per-account per-key counts. This makes candidate-set scoping a straight sum, and release/stale-reclaim/account-prune correctness falls out of the existing lease lifecycle (release pops the lease and reads its `api_key_id`; `_prune_runtime` already refuses to drop accounts with live leases). Rejected: a balancer-global `dict[api_key_id, int]` — cannot be scoped to a candidate set and needs separate GC.

### D4: Reuse the local-capacity denial machinery via one new stable reason

`api_key_stream_fair_share` is added to `_LOCAL_ACCOUNT_CAP_ERROR_CODES` (park-and-retry with keepalives; each retry re-evaluates the gate with fresh counters) and `LOCAL_OVERLOAD_CODES` (terminal 429 `rate_limit_error` + `Retry-After`). This is what makes the denial a bounded queue rather than a hard failure, with zero new transport code. The `Retry-After: 5` edge header vs 30s park interval mismatch is pre-existing for the account-cap codes and kept uniform.

### D5: Minimum guarantee is a hardcoded constant (2)

`MIN_GUARANTEE_STREAMS = 2` in `fair_share.py`. A key holding fewer than two streams is never denied by the gate, which makes starvation impossible: a denied requester by definition already holds >= 2 streams of work. Not a setting (P2 — no demonstrated need for tunability; the threshold is the operator lever).

### D6: Sticky commit-phase re-check; unbound path is atomic

The sticky path evaluates the gate in the filter-phase lock section and re-checks in the commit lock section (extending the existing `_account_lease_allowed_locked` re-check chain), because sticky DB I/O sits between the two sections and N concurrent selections for one key could otherwise overshoot the share by up to N — material for exactly the bursty-key workload this gate targets. The unbound path evaluates gate and acquires the lease inside one lock section; no re-check needed. Denial ordering in the sticky branch chain: fair-share denial precedes `hard_affinity_saturated` and the account-cap branch (broader per-key condition; both park in the same wait loop). Ownership/ambiguity direct errors still win — they are deterministic outcomes, not capacity.

### D7: Bypasses

(a) Keyless requests (`api_key is None`: key auth disabled, internal work) bypass the gate but their streams still count toward `T` — they consume real capacity. (b) Reattach-stage selection bypasses (threshold resolved to 0): reattach resumes an existing in-flight response and denying it strands running work; this mirrors the existing `stream_reserve_slots = 0` reattach carve-out. (c) Non-stream leases are out of scope by construction. (d) `stream_limit <= 0` (unlimited caps) leaves `C` undefined — gate inactive.

### D8: Per-replica, per-worker statistical scope

Counters, partitioned caps, and now fairness are all process-local. With W workers a key's effective share multiplies by at most W — identical in kind to the existing per-account cap model (single-worker-per-instance is already the spec'd deployment shape for shared caps). No new mechanism; documented in the setting description.

### D9: v1 exclusions (follow-ups)

Response-create lease fairness; key identity in `check_opportunistic_admission` for edge-fast denial; token-weighted shares (`estimated_tokens` already rides on leases); hysteresis band if threshold flapping proves noisy in practice (the 30s park interval already dampens it).

## Risks

- **Min-guarantee herd**: M fresh keys can each claim 2 slots under congestion; physical overcommit is still impossible (per-account caps bound total admission) — only fairness dilution among many small keys.
- **Hard-sticky continuations of a heavy key park mid-conversation** for up to the wait budget. Intended (continuations hold real slots); durable affinity rows are untouched; flagged here for maintainer confirmation.
- **Boundary flapping**: a heavy key at the threshold boundary oscillates admit/park at ~30s cadence. Accepted for v1 (D9).
- **Gauge semantics under multiprocess**: capacity uses `livemax` (every worker computes the same per-replica capacity), inflight uses default live-sum semantics (workers hold disjoint leases). Mirrors `cap_partition_replicas` rationale.

## Test plan

Pure math (`tests/unit/test_fair_share.py`): threshold boundaries (0, 100, exact `>=` edge), capacity floors/unlimited/zero-candidates, floor division, min-guarantee dominance, divisor requester-counting, denial message content. Balancer (`tests/unit/test_load_balancer_concurrency.py`): lease attribution and per-key count lifecycle (acquire/release/stale-reclaim/delete-at-zero, concurrent accuracy), default-off characterization, congested deny-heavy/admit-light, re-admission after release and after decongestion, keyless bypass with `T` counting, unbound single-lock atomicity, sticky commit re-check under concurrency, scoped-key capacity, response-create non-interference. Contract (`test_load_balancer.py` / `test_load_balancer_contract.py`): new kwargs defaulted — existing characterization unchanged; denial shape. Transport: park → keepalive → competitor release → re-admit; budget exhaustion → 429 + `Retry-After` + numbers in message. Settings: service pass-through, API round-trip incl. null-inherits-env and 0-100 rejection, audit trace, settings-reference regen. Frontend: schema/payload/component tests, three-locale keys.
