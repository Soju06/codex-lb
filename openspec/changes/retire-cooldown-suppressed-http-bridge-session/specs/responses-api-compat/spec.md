## MODIFIED Requirements

### Requirement: Durable retry-circuit state protects repeated hard-affinity failures

For a hard-affinity bridge key, the proxy MUST scope retry-circuit state by
affinity kind, affinity key, and API-key scope (using a stable anonymous scope
when no API key is present). The proxy MUST record only the documented
pre-response failure classes (`stream_incomplete`, `clean_close`, and
`stream_idle_timeout`).

A bridge retirement MUST record one of those failures only when the retiring
session still owns at least one pending request and no response event has been
observed for that request lifecycle. Retiring an idle upstream bridge with no
pending request MUST NOT advance the circuit or cause a later request to be
treated as a repeated failure. A pending request that has already emitted a
response event MUST remain excluded from this pre-response circuit.

When hard-key retry-circuit cooldown suppresses a request after its bridge
session has been created but before `response.create` is dispatched, and no
other turn owns that session (no visible pending or queued request, no
registered admission waiter, no unanchored handoff held by another request),
the proxy MUST mark the session `reconnect_requested` and `retire_after_drain`
and MUST invoke the bounded drain-retirement path before returning the
suppression error, so the never-dispatched socket is closed and detached rather
than left reusable. If another turn owns the session — in particular the
half-open probe the cooldown admitted, which may still sit between its
admission decision and its dispatch registration — the suppressed request MUST
leave the session unmarked and MUST NOT close it; that owner's own lifecycle
governs retirement, and the admitted turn MUST proceed. If the suppressed
request itself holds the session's unanchored handoff, the session counts as
unowned and its submit finalizer completes the retirement after releasing the
handoff. This applies to both late submit suppression and startup pre-submit
cooldown terminal handling.

So that a concurrent suppression can see it, a submit MUST count as a
registered admission waiter on its session from submit entry, before the
retry-circuit admission decision, until the dispatch path takes that
registration over; every pre-dispatch exit MUST release it, and the release
MUST re-run any retirement the registration was deferring.

Proof-gated full-resend replay and operation-fenced continuity replay remain
eligible bypasses and MUST NOT be retired by this suppression requirement.

The default circuit MUST open after two consecutive recorded failures. Once
open, it MUST suppress pre-created replay until the persisted cooldown expires,
using exponential backoff from sixty seconds up to ten minutes. Clean-close
failures MUST cap their cooldown at thirty seconds. The proxy MUST persist
failure count, cooldown deadline, last failure detail, and update time in the
`http_bridge_retry_circuits` table and MUST merge conflict updates so concurrent
replicas cannot shorten an existing cooldown.

The clean-close retry jitter maximum MUST be read from the
`http_responses_session_bridge_clean_close_retry_jitter_max_seconds` runtime
setting and MUST be bounded to the inclusive range 0–30 seconds.

The proxy MUST evict process-local circuit entries and their loaded/persisted
markers after one hour without use, independently of durable-row cleanup, so
one-shot hard-affinity keys cannot grow the worker's memory without bound.

Before every hard-affinity retry decision, the proxy MUST refresh the durable
row so a cooldown opened by another replica is observed even when this process
has already loaded the key. A durable lookup or persistence failure MUST NOT
crash the request; the proxy MUST continue using available local state and
record the failure for observability. Rows older than one hour MUST be treated
as expired and removed. A successful terminal response MUST clear the local
and durable circuit state.

#### Scenario: idle bridge retirement does not consume a circuit strike

- **GIVEN** a hard-affinity HTTP bridge has no pending requests
- **WHEN** its upstream WebSocket closes and the idle bridge is retired
- **THEN** the retry-circuit failure count for that key remains unchanged
- **AND** a later request is not placed in cooldown because of the idle close

#### Scenario: eventless pending retirement consumes exactly one strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no observed response event
- **WHEN** the bridge retires because the upstream fails before acknowledging the request
- **THEN** the retry circuit records exactly one failure for that request lifecycle

#### Scenario: midstream retirement does not consume a pre-response strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with an observed response event
- **WHEN** the bridge retires before completion
- **THEN** the pre-response retry-circuit failure count remains unchanged

#### Scenario: the second hard-key failure opens a durable circuit

- **GIVEN** a hard-affinity key has one recorded pre-response failure
- **WHEN** a second eligible failure is recorded
- **THEN** the proxy opens the retry circuit
- **AND** persists at least two consecutive failures and a cooldown deadline
- **AND** subsequent pre-created replay is suppressed until that deadline

#### Scenario: retry decisions observe a cooldown opened by another replica

- **GIVEN** this replica previously looked up a hard-affinity key with no row
- **AND** another replica persists an open cooldown for that same key and API-key scope
- **WHEN** this replica evaluates the next pre-created retry
- **THEN** it refreshes durable state before deciding
- **AND** suppresses the retry for the persisted cooldown

#### Scenario: circuit state remains isolated by key and API-key scope

- **GIVEN** one hard-affinity key has an open circuit
- **WHEN** a different affinity key or API-key scope evaluates a retry
- **THEN** that request is not suppressed by the first key's circuit

#### Scenario: durable circuit lookup failure does not fail the request

- **GIVEN** durable retry-circuit lookup or persistence is unavailable
- **WHEN** the proxy evaluates or records a retry-circuit event
- **THEN** the request continues using any available local circuit state
- **AND** the failure is logged and exposed through retry-circuit observability

#### Scenario: late cooldown suppression retires the newly created session

- **GIVEN** a hard-key request has created or selected an HTTP bridge session
- **AND** the retry circuit is still in cooldown when late pre-created
  admission runs
- **WHEN** the request is suppressed before `response.create` is dispatched
- **THEN** the proxy returns the existing HTTP 503 cooldown error
- **AND** marks the session for reconnect and retirement after drain
- **AND** invokes bounded retirement
- **AND** does not send `response.create` upstream
- **AND** the session is not reusable for a later request

#### Scenario: startup cooldown terminal handling retires its session

- **GIVEN** a hard continuity-bound request has an already-created bridge
  session but no safe replay bypass
- **AND** the retry circuit is in cooldown before the startup submit attempt
- **WHEN** startup terminal handling returns the cooldown failure
- **THEN** the existing 503 or synthetic `stream_idle_timeout` envelope is
  preserved
- **AND** the session is marked for reconnect and retirement after drain
- **AND** bounded retirement is invoked
- **AND** submit is not attempted

#### Scenario: cooldown replay bypass does not retire the session

- **GIVEN** a hard-key request is in cooldown
- **AND** proof-gated or operation-fenced continuity replay is allowed
- **WHEN** the retry decision runs
- **THEN** the request remains eligible for that authorized replay
- **AND** the generic cooldown suppression retirement is not triggered

#### Scenario: a concurrently admitted probe keeps its session

- **GIVEN** a hard-key retry circuit at cooldown expiry
- **AND** request A passes admission as the half-open probe and has not yet
  reached its dispatch registration
- **WHEN** request B on the same session is suppressed by A's probe lease
- **THEN** B returns the existing HTTP 503 cooldown error
- **AND** the session is not marked for reconnect or retirement and is not
  closed
- **AND** A proceeds past the pre-dispatch retiring fence to dispatch

#### Scenario: a session owned by other work is left to its owner

- **GIVEN** cooldown suppression rejects a request on a session that owns
  visible pending work, a registered admission waiter, or another request's
  unanchored handoff
- **WHEN** the suppression returns
- **THEN** the session is not marked for retirement and is not closed
- **AND** the owning work's own settlement path governs the session

#### Scenario: a suppressed request releases only its own admission registration

- **GIVEN** a submit counted itself as an admission waiter at entry
- **WHEN** the retry circuit suppresses it or any other pre-dispatch exit fails it
- **THEN** its registration is released without disturbing other waiters
- **AND** a retirement that registration was deferring runs once the session
  is unowned
