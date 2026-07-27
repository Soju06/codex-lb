# API Key Account-Pool Cutover and Affinity Generation Design

Date: 2026-07-27

## Context

Operators use the Dashboard to assign a bounded account pool to an API key, let that pool consume quota, and later replace it with another pool. These changes are infrequent but intentional cutovers.

Today, updating `assigned_account_ids` changes the API key scope and invalidates the API key cache, but existing `sticky_sessions` rows remain reachable. The sticky-session primary key does not include the API key identity or an account-assignment version. A Codex window can therefore keep resolving to an account that has been removed from the key's current pool.

HTTP bridge session-header keys and durable session-header aliases are also not assignment-generation aware. Versioning only `sticky_sessions` would therefore leave a second path that can resolve the removed account.

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
- Retire session-header-based bridge affinity at the same generation boundary as ordinary soft affinity.

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

An update increments the generation only when the normalized account assignment set actually changes. The account-assignment scope remains derived from whether the normalized set is empty. Submitting the same assignment set in another order does not increment the generation.

The API key row update, assignment replacement, generation increment, and changed timestamp are committed in one serialized transaction. The repository must prevent lost updates:

- PostgreSQL locks the API key row with `SELECT ... FOR UPDATE` before comparing and replacing assignments.
- SQLite enters the existing serialized writer section before reading the current assignments and keeps it through commit or rollback.

Cache invalidation and the cross-replica invalidation poller run only after the transaction commits.

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

### Legacy compatibility activation

Existing raw `CODEX_SESSION` rows are ambiguous: older builds stored both bare session-header affinity and hard turn-state ownership in the same key space. They cannot be safely reclassified in bulk.

The compatibility transition is therefore source- and generation-gated:

- generation `1` preserves the existing raw-row compatibility behavior;
- the first effective Dashboard account-pool change increments the key to generation `2`;
- a generation `2+` request whose affinity source is `session_header` does not consult raw legacy `CODEX_SESSION` rows;
- a request whose source is a real client `turn_state` continues to consult the legacy hard key space until turn-state ownership has a dedicated typed store;
- independently resolved response, file, or bridge ownership always outranks soft session-header affinity;
- mixed-version writers are prohibited once generation `2+` assignment cutovers are enabled.

This is the activation boundary that restores mobility without guessing the provenance of existing raw rows. Legacy cleanup begins only after every supported process writes v2 keys and the maximum legacy retention window has elapsed.

### Versioned bridge session-header affinity

HTTP bridge session keys and durable session-header aliases use the same API key assignment generation as soft sticky keys.

- New bridge session-header keys include API key ID and assignment generation.
- Durable `session_header` aliases include the generation and cannot be resolved by a later generation.
- Old unversioned session-header bridge aliases are consulted only for generation `1`.
- Generation `2+` requests may still resolve the old bridge through authoritative `previous_response_id`, file, or verified turn-state ownership during the drain window.
- Assignment cutover does not delete the old bridge immediately; it removes session-header reachability for new soft work while preserving typed hard aliases for draining.

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

Drain reuses the existing required-continuity-owner selection path. Only a proven hard owner may set `required_continuity_owner=True`. The assignment timestamp and drain deadline gate whether that owner may bypass the current `assigned_account_ids`; soft affinity never sets this flag.

This makes Dashboard changes take effect immediately for new work while allowing existing stateful operations to finish.

The drain duration is configured by:

```text
api_key_account_assignment_drain_seconds = 1800
```

Zero disables draining and produces an immediate hard cutover.

### Unavailable hard owner

If a proven hard owner is unavailable, removed after the drain deadline, or rate-limited:

1. Fail selection immediately without waiting on account-cap or response-create gates.
2. The request surface determines whether the request is demonstrably portable and whether the upstream has acknowledged or executed it.
3. If that surface proves portability before acknowledgement, retry once as a fresh request in the current generation.
4. Otherwise return the typed result `continuity_reset_required`.

A request is portable only when it contains no:

- `previous_response_id`
- account-owned file ID
- durable bridge dependency
- unverified opaque turn state
- upstream output reference that requires the old owner

Replay is not a generic load-balancer responsibility. Direct streaming, compact, and HTTP bridge paths each map their existing owner-unavailable outcome into the shared selection result and apply their own current acknowledgement and projection rules. Existing exact-code replay gates, including the HTTP bridge `previous_response_owner_unavailable` path, remain valid until that surface is explicitly migrated and covered by regression tests.

The 15956 shim may translate `continuity_reset_required` into a client-safe reconnect or new-session signal. It must not silently remove hard references or replay a request after upstream acknowledgement, because that could duplicate tools or create divergent responses.

The system cannot guarantee semantic preservation when the only copy of conversation state belongs to an unavailable upstream account. The guarantee is that soft requests cut over normally and true hard failures are bounded, explicit, and never misreported as general account exhaustion.

## Request Flow

### Soft request after a Dashboard pool change

1. Dashboard updates the API key assignments.
2. The transaction increments `account_assignment_generation`.
3. API key caches are invalidated after commit.
4. Other replicas observe the new generation through the existing invalidation poller, bounded by its configured poll interval.
5. The next request builds a v2 soft-affinity and bridge session-header key using the new generation.
6. No mapping exists for that generation.
7. The load balancer selects a healthy account from the new pool.
8. A new soft mapping is persisted.

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

No bulk rewrite of `sticky_sessions` or bridge aliases is required. V2 keys naturally coexist with existing rows. A later bounded cleanup can delete expired legacy rows and aliases only after all supported builds generate v2 keys and the maximum retention interval has elapsed.

Rolling deployment requirements:

- all writers and bridge registries must understand the generation before the Dashboard is allowed to change assignments;
- readers that do not understand generation must not run after the migration is activated;
- generation `2+` cutovers remain disabled until the deployment coordinator confirms that no mixed-version writer is live;
- production startup scripts must continue to target `repo-upstream-migration` so older ORM and migration code cannot open the upgraded database.

## Observability

Account-selection logs and metrics must include:

- API key ID
- assignment generation
- affinity source
- affinity strength (`none`, `soft`, `hard`)
- affinity namespace version
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
- legacy bridge session-header aliases ignored

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
2. Concurrent assignment changes serialize without a lost generation increment or incorrect drain timestamp.
3. Reordering or resubmitting the same assignment set does not increment generation.
4. An old soft mapping is unreachable after generation changes.
5. A first-turn session-header request selects from the new pool after invalidation propagation.
6. A generation `2+` session-header request ignores a legacy raw row, while generation `1` and real turn-state requests preserve compatibility behavior.
7. A generation `2+` bridge session-header alias cannot resolve an old-generation bridge.
8. An old bridge remains reachable through a proven hard alias during the drain window.
9. Another API key using the same client session ID remains unaffected.
10. A proven hard owner outside the new pool remains usable during the drain window through the required-continuity-owner path.
11. New first turns cannot use removed drain-only accounts.
12. Drain access ends after 30 minutes.
13. A rate-limited hard owner fails immediately without gate waiting.
14. Each request surface proves portable pre-acknowledgement replay independently and retries at most once.
15. `previous_response_id`, file, durable bridge, and opaque turn-state requests never cross accounts without a verified portable reconstruction.
16. Temporary soft spill does not overwrite the stable mapping.
17. Client disconnect cleans pending requests, leases, generators, and gates.
18. API key cache invalidation exposes the new generation across replicas within the configured poll bound.

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
2. Add generation-aware sticky and bridge session-header namespaces while preserving generation `1` compatibility.
3. Confirm every live writer and bridge registry understands v2 before enabling generation `2+` cutovers.
4. Enable generation-aware cutover in the isolated 2456 environment.
5. Validate a Dashboard pool change while an ordinary Codex window remains open.
6. Validate hard drain using a synthetic authoritative owner.
7. Validate owner-unavailable behavior separately for direct streaming, compact, HTTP bridge, and shim handling.
8. Deploy to 2455 only after all automated and isolated smoke checks pass.
9. Monitor cutover, drain, reset, legacy-ignore, and hard-affinity metrics through at least one real pool rotation.

## Success Criteria

- Changing a Key's assigned accounts makes ordinary open Codex windows use the new pool on their next soft request after bounded cache-invalidation propagation.
- The reproduced first-turn image request does not return `hard_affinity_saturated`.
- Old-generation sticky and bridge session-header aliases cannot pin new soft work to removed accounts.
- Existing hard sessions can finish on a healthy old owner for up to 30 minutes.
- A removed or exhausted hard owner never causes an unbounded wait.
- No request carrying a proven account-local object is silently sent to another account.
- Other API keys experience no affinity change.
