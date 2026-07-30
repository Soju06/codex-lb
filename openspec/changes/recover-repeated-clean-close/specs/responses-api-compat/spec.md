## MODIFIED Requirements

### Requirement: Clean upstream close before any response event fails fast

When the HTTP Responses bridge observes an upstream WebSocket close with
`close_code = 1000` before any `response.*` event has been surfaced for the
pending request, the proxy MUST preserve its existing pre-visible replay
guards. If the request has already used exactly one eligible pre-visible
replay and the replacement upstream WebSocket also closes cleanly before any
response event, the proxy MAY perform exactly one additional replay. The
additional replay MUST be hard-capped at one per request, and the configured
maximum MUST NOT raise that cap.

The proxy MUST NOT replay after downstream-visible output, after a terminal
response event, or when continuity-sensitive request state makes replay unsafe.
Before the additional replay, the proxy MAY sleep for bounded configured
jitter. The proxy MUST emit a dedicated low-cardinality diagnostic event for
the additional replay.

When a downstream HTTP stream task initiates pre-response recovery while the
upstream reader is blocked on the superseded socket, the proxy MUST cancel and
await that reader before locally closing the socket. It MUST then start exactly
one reader for the replacement socket. A close caused by replacing the socket
MUST NOT be recorded as an upstream clean-close failure, MUST NOT increment the
retry circuit, and MUST NOT retire pending work moved to the replacement. The
cancelled reader's socket-generation finalizer MUST NOT leave the shared session
marked closed while the replacement socket is being selected or opened, so idle
pruning MUST NOT evict the handoff in progress.

The default pre-response idle-recovery window MUST leave bounded headroom
before the downstream client's request timeout. With the default ten-second
keepalive interval, the proxy MUST initiate eligible recovery after no more
than six silent intervals so replacement connection and first output can occur
before a 120-second client deadline.

The stuck pre-response watchdog MUST judge staleness using elapsed time since
the last upstream activity and the absence of a response identifier or
`response.created` latency, not admission flags alone. A request with a prior
continuity anchor MUST receive at most two retire-thresholds of grace before
being considered stale. When the watchdog skips a candidate, it MUST emit a
low-cardinality diagnostic containing the session-closed state, candidate
count, and pending-state verdicts.

#### Scenario: clean close before response output receives one bounded additional replay

- **GIVEN** an HTTP bridge request has no surfaced `response.*` events
- **AND** its first pre-visible replay has already been used
- **WHEN** the replacement upstream WebSocket closes with code `1000`
- **THEN** the proxy performs one additional pre-visible replay
- **AND** the request replay count increases by one
- **AND** the proxy emits a `retry_precreated_clean_close` diagnostic event

#### Scenario: repeated clean closes do not create an unbounded replay loop

- **GIVEN** the additional clean-close replay has already been used
- **WHEN** another upstream WebSocket closes cleanly before response output
- **THEN** the proxy does not replay the request again
- **AND** the existing terminal or circuit handling is used

#### Scenario: visible output still prevents clean-close replay

- **GIVEN** the pending request has surfaced any response event downstream
- **WHEN** the upstream WebSocket closes with code `1000`
- **THEN** the proxy does not replay the request

#### Scenario: clean-close retry jitter is bounded

- **GIVEN** clean-close retry jitter is configured
- **WHEN** the additional clean-close replay is scheduled
- **THEN** the delay is no greater than the configured jitter maximum
- **AND** the hard replay cap remains one regardless of the configured value

#### Scenario: downstream idle recovery transfers reader ownership

- **GIVEN** the upstream reader is blocked on the current bridge socket
- **AND** the downstream HTTP stream task initiates eligible pre-response recovery
- **WHEN** the bridge replaces the upstream socket
- **THEN** the old reader is cancelled and awaited before its socket is closed
- **AND** the shared session remains live while the replacement socket opens
- **AND** idle pruning retains the registered session while the handoff is in progress
- **AND** exactly one reader owns the replacement socket
- **AND** the local close does not open or increment the retry circuit
- **AND** pending work remains attached to the replacement session

#### Scenario: silent pre-response recovery precedes the client timeout

- **GIVEN** the upstream has produced no response event
- **AND** the default ten-second keepalive interval is active
- **WHEN** six silent intervals elapse
- **THEN** the proxy initiates eligible pre-response recovery
- **AND** at least sixty seconds remain before a 120-second client request timeout

#### Scenario: anchored stuck-gate grace is bounded

- **GIVEN** a pending HTTP bridge request has a prior continuity anchor
- **AND** no response identifier or `response.created` latency has been recorded
- **WHEN** less than two retire thresholds have elapsed since the gate began waiting
- **THEN** the watchdog does not classify the request as stale
- **WHEN** two retire thresholds elapse without upstream activity
- **THEN** the watchdog may classify the request as stale

#### Scenario: upstream activity resolves admission-flag ambiguity

- **GIVEN** a pending request has not acquired the response-created gate
- **AND** upstream activity has not produced a response identifier or `response.created`
- **WHEN** the staleness threshold elapses
- **THEN** the watchdog classifies the request as stale
- **AND** emits pending-state verdict inputs when it skips a watchdog pass

### Requirement: Durable retry-circuit state protects repeated hard-affinity failures

For a hard-affinity bridge key, the proxy MUST scope retry-circuit state by
affinity kind, affinity key, and API-key scope (using a stable anonymous scope
when no API key is present). The proxy MUST record only the documented
pre-response failure classes (`stream_incomplete`, `clean_close`, and
`stream_idle_timeout`).

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

### Requirement: Upstream websocket drops penalize affected accounts

When an upstream websocket closes while one or more streamed response requests
are pending and have not reached a terminal event, the proxy MUST record a
transient upstream error for the account before signaling failure for those
pending requests, except when the close carries a classified process-wide
network failure, is a clean close (`close_code = 1000`) before any
`response.*` event, or carries the classified per-socket
`upstream_keepalive_timeout` transport error. Clean pre-response closes and
keepalive timeouts MUST remain account-neutral while using the bounded retry
and retry-circuit handling above. A classified process-wide network failure
MUST remain account neutral and use its network error code. For other closes,
the proxy MUST surface
`stream_incomplete` to affected pending requests.

#### Scenario: clean pre-response close does not penalize the account

- **GIVEN** a hard-affinity HTTP bridge request is pending with no surfaced response event
- **WHEN** the upstream websocket closes cleanly before response output
- **THEN** the proxy records the clean-close retry-circuit outcome
- **AND** the selected account is not penalized
