# responses-api-compat Delta Specification

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

The default circuit MUST open after two consecutive recorded failures. Once
open, it MUST suppress pre-created replay until the persisted cooldown expires,
using exponential backoff from sixty seconds up to ten minutes. Clean-close
failures MUST cap their cooldown at thirty seconds. The proxy MUST persist
failure count, cooldown deadline, last failure detail, and update time in the
`http_bridge_retry_circuits` table and MUST merge conflict updates so concurrent
replicas cannot shorten an existing cooldown.

When the cooldown expires, the proxy MUST admit exactly one probe request and
MUST hold a half-open lease that suppresses other non-bypassed requests for
that key while the probe is outstanding. The half-open lease MUST be no longer
than the base backoff (sixty seconds), because a failing probe records a
failure that clears the lease and arms a fresh cooldown, so a longer lease only
extends the window in which an unrecorded probe failure leaves the key
suppressed. If the circuit's last recorded failure is an eventless poison class
(`stream_incomplete` or `stream_idle_timeout` with no observed response event),
the proxy MUST abandon the session's durable continuity anchor before admitting
the probe, so the probe resends the captured full history rather than the
anchored request the circuit was opened on. A `clean_close` last failure MUST
leave the anchor intact.

When the proxy suppresses a submission, the `retry_after_seconds` it returns
and the detail it logs MUST reflect the timer that is actually refusing the
request: the cooldown while the cooldown is active (`hard_key_cooldown`), and
the half-open lease once the cooldown has expired (`hard_key_half_open`). The
suppression message MUST NOT describe the bridge as cooling down when the
cooldown has expired.

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

#### Scenario: half-open probe abandons a poisoned anchor before dispatch

- **GIVEN** a hard-affinity key's circuit is open with `stream_incomplete` as its last recorded eventless failure
- **AND** the session holds a durable continuity anchor
- **WHEN** the cooldown expires and the next request arrives
- **THEN** the proxy clears the durable continuity anchor through the fenced durable write
- **AND** admits that request as the probe so it resends full history without the anchor

#### Scenario: half-open probe preserves the anchor after a clean close

- **GIVEN** a hard-affinity key's circuit is open with `clean_close` as its last recorded failure
- **WHEN** the cooldown expires and the next request arrives
- **THEN** the probe is admitted with the durable continuity anchor intact

#### Scenario: half-open lease is bounded by the base backoff

- **GIVEN** a probe has been admitted for a hard-affinity key
- **WHEN** other non-bypassed requests for that key arrive
- **THEN** they are suppressed for no longer than the base backoff
- **AND** a recorded probe failure clears the lease and opens a fresh cooldown

#### Scenario: suppression reports the half-open lease after the cooldown expires

- **GIVEN** a hard-affinity key's cooldown has expired and a probe holds the half-open lease
- **WHEN** another request for that key is suppressed
- **THEN** the 503 `retry_after_seconds` reflects the remaining half-open lease
- **AND** the circuit event detail is `hard_key_half_open`
- **AND** the message does not describe the bridge as cooling down

#### Scenario: suppression reports the cooldown while cooling

- **GIVEN** a hard-affinity key's cooldown is active
- **WHEN** a request for that key is suppressed
- **THEN** the 503 `retry_after_seconds` reflects the remaining cooldown
- **AND** the circuit event detail is `hard_key_cooldown`

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

### Requirement: Repeated zero-event idle failures poison dead anchors

For hard HTTP bridge keys, repeated zero-event idle failures MUST use the
existing durable retry-circuit counter to identify an anchor that should no
longer remain addressable. When consecutive failures for the same hard bridge
key reach the configured poison threshold, the proxy MUST abandon durable
continuity for that session and retire the bridge even when admission waiters
exist. The default threshold MUST be no greater than seven failures.

Recovery MUST NOT depend on the counter reaching that threshold: because an
open circuit admits only one probe per half-open lease, the counter may never
reach a threshold above the circuit's own opening threshold from an interactive
client. The retry circuit's half-open transition MUST therefore abandon the
anchor independently, as specified under the durable retry-circuit
requirement, so a dead anchor is dropped on the first probe after the circuit
opens.

#### Scenario: Admission waiters cannot defer anchor poisoning forever

- **GIVEN** a hard durable bridge key has admission waiters
- **AND** repeated zero-event idle failures for that same key reach the poison
  threshold
- **WHEN** the reader failure path would normally defer retirement for the
  admission waiter
- **THEN** the proxy clears the durable continuity anchors
- **AND** retires the session despite the admission waiter
- **AND** the next attach starts from fresh durable state rather than the
  poisoned previous-response anchor

#### Scenario: A dead anchor is dropped before the poison threshold is reached

- **GIVEN** a hard durable bridge key has two consecutive eventless `stream_incomplete` failures and an open circuit
- **AND** the configured poison threshold is greater than two
- **WHEN** the cooldown expires and the next request is admitted as the probe
- **THEN** the durable continuity anchor has already been abandoned
- **AND** the probe resends full history rather than the dead anchor

#### Scenario: Lease liveness comparison is timezone-safe
- **GIVEN** a durable bridge session whose `lease_expires_at` was read from a `timestamptz` column (offset-aware) on PostgreSQL
- **WHEN** the dead-owner classifier evaluates lease liveness against the application's naive-UTC clock
- **THEN** both timestamps MUST be normalized to naive UTC before comparison
- **AND** the anchored-lookup path MUST NOT raise on mixed-awareness datetimes
