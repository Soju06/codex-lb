# API Key Account-Pool Cutover and Affinity Generation Design

Date: 2026-07-27

## Context

Operators use the Dashboard to assign a bounded account pool to an API key, let that pool consume quota, and later replace it with another pool. These changes are infrequent but intentional cutovers.

Today, updating `assigned_account_ids` changes the API key scope and invalidates the API key cache, but existing `sticky_sessions` rows remain reachable. The sticky-session primary key does not include the API key identity or an account-assignment version. A Codex window can therefore keep resolving to an account that has been removed from the key's current pool.

The resulting selection failure is misleadingly reported as `hard_affinity_saturated`, even when the request is a first turn with no authoritative response, file, or bridge owner. Production request `05904fd2-0f1b-4b46-a65b-14e7478282da` demonstrated this shape:

- `request_stage=first_turn`
- `preferred_account_id=None`
- fourteen accounts in the current API key scope
- selection rejected because a legacy sticky owner was unavailable

The system needs an explicit cutover boundary between account-pool generations instead of treating old and new assignments as one timeless affinity namespace.

## Goals

- Route new and soft-affinity requests through the newly assigned account pool immediately after a Dashboard change.
- Allow proven hard-continuity requests to drain on their old owner for at most 30 minutes.
- Prevent an old soft sticky mapping from producing `hard_affinity_saturated` after a pool change.
- Keep `previous_response_id`, uploaded-file, durable bridge, and verified turn-state ownership fail-closed across accounts.
- Avoid overwriting the previous soft mapping when a request temporarily spills to another account.
- Keep unrelated API keys and their sessions unaffected by a pool change.
- Make unavoidable continuity loss explicit and fast instead of returning a generic 502 or waiting on a gate.

## Non-goals

- Transparently migrate an account-local upstream response, file, or opaque turn state after its owner becomes unavailable.
- Allow every request to move freely between accounts.
- Reconstruct complete conversations from request logs.
- Delete all legacy sticky rows during startup.
- Change account quota, rate-limit, or model-entitlement policies.

## Decision

Introduce a versioned soft-affinity namespace per API key and treat assignment changes as controlled routing cutovers.

### API key assignment generation

Add the following persisted fields to `api_keys`:

- `account_assignment_generation`: monotonically increasing integer, default `1`
- `account_assignment_changed_at`: timestamp of the most recent effective account-pool change

An update increments the generation only when the normalized account assignment set or its enabled/disabled scope state actually changes. Submitting the same assignment set in another order does not increment the generation.

The API key row update, assignment replacement, generation increment, and changed timestamp are committed in one transaction. Cache invalidation and the cross-replica invalidation poller run only after the transaction commits.

### Versioned soft-affinity keys

Soft affinity includes:

- `x-codex-session-id` without authoritative account-local continuity
- prompt-cache affinity
- sticky-thread affinity

The internal selection key includes:

- API key ID
- account-assignment generation
- affinity source
- a digest of the client-provided affinity value

Conceptually:

```text
codex-lb-affinity-v2:
  api-key/<key-id>:
  generation/<generation>:
  source/<source>:
  value/<sha256>
```

The raw API key secret and raw client affinity values are never persisted.

After a Dashboard pool change, old soft mappings remain in the database until normal expiry or cleanup, but they become unreachable because the generation changes. The first request in the new generation selects from the new account pool and creates a new soft mapping.

Legacy raw `CODEX_SESSION` rows must not impose hard ownership on a v2 session-header request that has no independently proven continuity owner.

### Hard-continuity evidence

Hard continuity must be based on typed, authoritative evidence rather than the presence of a generic sticky row.

Hard evidence includes:

- resolved `previous_response_id` owner
- resolved uploaded-file owner
- durable HTTP/WebSocket bridge owner
- verified, persisted client turn-state owner

Soft session-header and prompt-cache mappings are never authoritative owners.

When multiple hard sources disagree, selection remains fail-closed with a specific `continuity_owner_conflict` result.

### Thirty-minute drain window

When an API key account pool changes:

- new first-turn and soft-affinity requests use only the new generation and new account pool;
- a proven hard-continuity request may use its old owner outside the new pool until `account_assignment_changed_at + 30 minutes`;
- the old owner must still be active, healthy, model-compatible, and not rate-limited;
- drain access never creates a new soft mapping to the removed account;
- drain access is limited to the API key whose assignment changed.

This makes Dashboard changes take effect immediately for new work while allowing existing stateful operations to finish.

The drain duration is configured by:

```text
api_key_account_assignment_drain_seconds = 1800
```

Zero disables draining and produces an immediate hard cutover.

### Unavailable hard owner

If a proven hard owner is unavailable, removed after the drain deadline, or rate-limited:

1. Fail selection immediately without waiting on account-cap or response-create gates.
2. If the request is demonstrably portable and the upstream has not acknowledged or executed it, retry once as a fresh request in the current generation.
3. Otherwise return the typed result `continuity_reset_required`.

A request is portable only when it contains no:

- `previous_response_id`
- account-owned file ID
- durable bridge dependency
- unverified opaque turn state
- upstream output reference that requires the old owner

The 15956 shim may translate `continuity_reset_required` into a client-safe reconnect or new-session signal. It must not silently remove hard references or replay a request after upstream acknowledgement, because that could duplicate tools or create divergent responses.

The system cannot guarantee semantic preservation when the only copy of conversation state belongs to an unavailable upstream account. The guarantee is that soft requests cut over normally and true hard failures are bounded, explicit, and never misreported as general account exhaustion.

## Request Flow

### Soft request after a Dashboard pool change

1. Dashboard updates the API key assignments.
2. The transaction increments `account_assignment_generation`.
3. API key caches are invalidated after commit.
4. The next request builds a v2 soft-affinity key using the new generation.
5. No mapping exists for that generation.
6. The load balancer selects a healthy account from the new pool.
7. A new soft mapping is persisted.

### Existing hard request during drain

1. The request resolves an authoritative owner outside the new pool.
2. The assignment change is still within the 30-minute drain window.
3. The old owner is healthy and compatible.
4. Selection uses the old owner for this hard continuation only.
5. No new first turn or soft mapping is assigned to that owner.

### Existing hard request after owner loss

1. The request resolves an authoritative owner that cannot be used.
2. Selection does not wait for unrelated accounts or gates.
3. A portability check determines whether a fresh replay is safe.
4. Portable requests select from the current pool once.
5. Non-portable requests return `continuity_reset_required`.

## Data and Compatibility

The schema migration adds the two API key columns with backward-compatible defaults. Existing keys start at generation `1`.

No bulk rewrite of `sticky_sessions` is required. V2 keys naturally coexist with existing rows. A later bounded cleanup can delete expired legacy rows after all supported builds generate v2 keys.

Rolling deployment requirements:

- all writers must understand the generation before the Dashboard is allowed to change assignments;
- readers that do not understand generation must not run after the migration is activated;
- production startup scripts must continue to target `repo-upstream-migration` so older ORM and migration code cannot open the upgraded database.

## Observability

Account-selection logs and metrics must include:

- API key ID
- assignment generation
- affinity source
- affinity strength (`none`, `soft`, `hard`)
- authoritative owner evidence type
- whether the owner is inside the current pool
- whether drain access was used
- fallback decision and rejection reason

New counters:

- soft affinity cutovers
- hard drain selections
- portable fresh replays
- continuity resets required
- legacy raw affinity rows ignored

These fields must not include raw API keys, session headers, turn states, or prompt-cache values.

## Error Handling

- Assignment updates are atomic; a failed assignment write does not increment generation.
- Cache invalidation failure after commit is retried through the existing invalidation mechanism and logged with the committed generation.
- Hard-owner conflicts remain fail-closed.
- Soft-affinity misses fall through to normal selection.
- A temporary soft spill does not rewrite the stable mapping.
- `hard_affinity_saturated` is reserved for a proven hard owner blocked by concurrency capacity, not legacy or soft affinity.
- `continuity_owner_unavailable` describes a proven owner that is unhealthy or rate-limited.
- `continuity_reset_required` describes a non-portable request whose previous owner cannot be used.

## Test Strategy

At minimum, regression coverage must prove:

1. An effective assignment change increments generation atomically.
2. Reordering or resubmitting the same assignment set does not increment generation.
3. An old soft mapping is unreachable after generation changes.
4. A first-turn session-header request immediately selects from the new pool.
5. A legacy raw sticky row cannot turn a v2 soft request into hard affinity.
6. Another API key using the same client session ID remains unaffected.
7. A proven hard owner outside the new pool remains usable during the drain window.
8. New first turns cannot use removed drain-only accounts.
9. Drain access ends after 30 minutes.
10. A rate-limited hard owner fails immediately without gate waiting.
11. A portable, pre-acknowledgement request can retry once in the new pool.
12. `previous_response_id`, file, durable bridge, and opaque turn-state requests never cross accounts without a verified portable reconstruction.
13. Temporary soft spill does not overwrite the stable mapping.
14. Client disconnect cleans pending requests, leases, generators, and gates.
15. API key cache invalidation exposes the new generation across replicas.

Verification before production:

- targeted unit and integration tests
- full proxy and API key suites
- Ruff and formatting checks
- type checking
- proxy architecture checks
- isolated port 2456 smoke test against a copied production database
- Dashboard assignment cutover test with two synthetic account pools
- no replacement of production port 2455 until the 2456 checks pass

## Rollout

1. Add observability and the generation schema behind an inactive code path.
2. Enable generation-aware soft keys in the isolated 2456 environment.
3. Validate a Dashboard pool change while an ordinary Codex window remains open.
4. Validate hard drain using a synthetic authoritative owner.
5. Validate owner-unavailable behavior and shim handling.
6. Deploy to 2455 only after all automated and isolated smoke checks pass.
7. Monitor cutover, drain, reset, and hard-affinity metrics through at least one real pool rotation.

## Success Criteria

- Changing a Key's assigned accounts makes ordinary open Codex windows use the new pool on their next soft request.
- The reproduced first-turn image request does not return `hard_affinity_saturated`.
- Existing hard sessions can finish on a healthy old owner for up to 30 minutes.
- A removed or exhausted hard owner never causes an unbounded wait.
- No request carrying a proven account-local object is silently sent to another account.
- Other API keys experience no affinity change.

