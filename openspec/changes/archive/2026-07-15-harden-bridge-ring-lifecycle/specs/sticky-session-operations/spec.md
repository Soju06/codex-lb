# sticky-session-operations Delta

## ADDED Requirements

### Requirement: Durable bridge lease writes are fenced
All durable HTTP bridge session lease writes — renewal, release, and continuity-alias registration — MUST be executed as single fenced statements conditioned on the caller's `(owner_instance_id, owner_epoch)` so a fenced-out caller mutates nothing. A fenced-out renewal or release MUST leave the row (owner, lease, state, and `latest_turn_state` / `latest_response_id` continuity anchors) unchanged and MUST report the current owner snapshot to the caller.

#### Scenario: Stale-epoch renewal does not overwrite the new owner
- **GIVEN** replica B took over a durable bridge session, advancing its owner epoch
- **WHEN** replica A renews the session with its stale epoch
- **THEN** the row still shows replica B's ownership, lease, and continuity anchors
- **AND** replica A receives a snapshot identifying replica B as the current owner

#### Scenario: Stale-epoch release does not clear the new owner's lease
- **GIVEN** replica B took over a durable bridge session, advancing its owner epoch
- **WHEN** replica A releases the session with its stale epoch
- **THEN** the row keeps replica B's ownership and ACTIVE state
- **AND** replica A receives a snapshot identifying replica B as the current owner

### Requirement: Fenced-out replicas evict their local bridge session
When a replica discovers through a fenced renewal or fenced alias write that another instance or epoch owns the durable session, it MUST close its local in-memory bridge session — closing the upstream websocket and releasing the account lease — instead of adopting the new epoch and continuing to serve. A replica MUST also reconcile durable ownership for local sessions whose lease is past its TTL on the ring-heartbeat cadence and close any session that has been fenced out, so orphaned upstream connections and account leases are bounded by the lease TTL rather than the idle TTL.

#### Scenario: Fenced-out renewal closes the local session
- **GIVEN** replica A holds a local bridge session and replica B took over the durable row
- **WHEN** replica A's lease renewal is fenced out
- **THEN** replica A detaches and closes the local session, releasing its account lease and upstream websocket
- **AND** the request fails with the retryable bridge-instance-mismatch error instead of riding the fenced-out session

#### Scenario: Heartbeat reconciliation closes fenced-out idle sessions
- **GIVEN** replica A holds an idle local bridge session whose durable lease expired
- **AND** replica B has since claimed the durable row
- **WHEN** replica A's heartbeat reconciliation sweep runs
- **THEN** replica A closes the fenced-out local session
- **AND** local sessions still owned by replica A are left untouched

#### Scenario: Reconciliation lookups survive large candidate sets
- **GIVEN** more local sessions are past the lease TTL than fit in one database `IN (...)` parameter list
- **WHEN** the reconciliation sweep batch-loads the durable rows
- **THEN** the lookup is chunked so every candidate resolves and fenced-out sessions are still evicted

### Requirement: Abandoned durable bridge rows are purged
The background cleanup loop MUST delete ACTIVE and DRAINING `http_bridge_sessions` rows whose lease is expired and whose `last_seen_at` predates the retention cutoff, deleting their aliases in the same pass, so crashed-owner and abandoned-drain rows do not accumulate. Rows with an unexpired lease or recent activity MUST NOT be deleted so crash takeover and drain recovery keep their continuity anchors. The retention cutoff MUST be at least the longest effective bridge session reuse window — the maximum of the prompt-cache affinity max age, the prompt-cache bridge idle TTL, the codex bridge idle TTL, and the base bridge idle TTL — so an idle-but-still-reusable local session never loses its ACTIVE durable row and aliases while it can still be reused.

#### Scenario: Expired abandoned rows are purged with their aliases
- **WHEN** the cleanup loop runs
- **AND** an ACTIVE row's lease expired and its `last_seen_at` is older than the retention cutoff
- **THEN** the row and its aliases are deleted

#### Scenario: Recent or live-lease rows survive the purge
- **WHEN** the cleanup loop runs
- **AND** a row holds an unexpired lease or has `last_seen_at` within the retention cutoff
- **THEN** the row and its aliases are preserved

#### Scenario: In-reuse-window prompt-cache sessions keep their durable row
- **GIVEN** the prompt-cache bridge idle TTL exceeds the prompt-cache affinity max age
- **WHEN** the cleanup loop runs against an ACTIVE row whose lease expired but whose `last_seen_at` is within the prompt-cache bridge idle TTL
- **THEN** the row and its aliases are preserved so a local reuse keeps its durable ownership and continuity anchors
