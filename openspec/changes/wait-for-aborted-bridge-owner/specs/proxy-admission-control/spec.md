## MODIFIED Requirements

### Requirement: HTTP bridge startup admission waits are bounded

The proxy MUST apply the configured proxy admission wait timeout to each HTTP
bridge startup wait attempt for per-session response-create gate acquisition,
bridge capacity waiters, and in-flight session creation waiters.

For per-session response-create gate acquisition by a bridged Responses request,
an expired gate acquisition attempt MUST be treated as a recoverable capacity
wait rather than a terminal failure: the request MUST release its queue slot and
account lease, wait with capacity-wait progress semantics, and retry gate
acquisition, bounded by the bridge request budget. Requests eligible for
soft-affinity reroute MUST still attempt the reroute before entering the
recoverable wait. When the bridge request budget is exhausted before the gate
opens, the proxy MUST reject the request locally with HTTP 429,
`error.code = "response_create_gate_timeout"`, and the stable local-overload
reason.

For bridge capacity waiters and in-flight session creation waiters, when the
configured proxy admission wait timeout expires the proxy MUST reject the
request locally with HTTP 429 and a structured local-overload error envelope
using `error.code = "capacity_exhausted_active_sessions"` unless exact-owner
observation proves a retry-safe creator terminated inside one additional
configured admission-wait interval. Timing out while
observing another request's pending in-flight session creation MUST evict or
abort that in-flight marker when it is still pending so later requests can
attempt a fresh bridge session instead of waiting on the same stalled future.

If the timed-out marker records an exact non-handoff owner task, the proxy MUST
signal only that owner and retain the aborted marker while that owner is still
running. If that exact owner terminates within one additional configured
admission-wait interval and the key is retry-safe, the timing-out request MUST
retry admission. If the owner does not terminate in that interval, the proxy
MUST return the existing structured local-overload HTTP 429 and leave the
owner-held marker registered until that owner finalizes. The proxy MUST NOT use
this retry path to replace generated turn-state in-flight creation. The proxy
MUST classify generated turn-state from recorded provenance rather than key
text; client-supplied values matching generated prefixes MUST remain explicit
turn-state, and a client-echoed turn-state on the Responses surface MUST be
classified from the alias the origin recorded when it minted that value. Signed
owner forwarding MUST preserve generated turn-state provenance across the
origin-to-owner boundary and the receiver MUST apply it to the forwarded bridge
key. The provenance proof MUST bind the tools-bound body proof that the
receiver independently validated for that exact request; stripping or
transplanting either proof MUST NOT grant generated-state privilege. Missing
legacy provenance and client-supplied headers MUST NOT upgrade explicit
turn-state to generated.
Generated turn-state creator timeouts keep the existing structured
local-overload HTTP 429 behavior and the late owner MUST NOT return an
unregistered bridge session to the caller.

If a request owns in-flight bridge session creation and is cancelled or fails
after publishing the in-flight marker but before registering the created
session, the proxy MUST remove or settle that in-flight marker. A settled
marker that stays registered while its owner closes the created session is a
capacity placeholder only: an admission waiter MUST NOT adopt its terminal state
as the waiter's own outcome and MUST NOT re-enter admission on it without
waiting. A capacity waiter for an unrelated key MUST observe the marker's owner
within the bounded owner-observation interval and then retry admission or
return the structured local-overload HTTP 429; the creator's own rejection
(for example an account quota error) MUST NOT be returned to that waiter. A
same-key waiter MUST treat a cancelled marker the same way and MAY adopt a
creator's terminal error. The stale in-flight sweeper MUST reclaim a settled
marker whose owner is still running once the marker exceeds the stale
threshold. If a session owner later finishes creation after its in-flight
marker was evicted, the owner MUST NOT return an unregistered bridge session to
the caller.

#### Scenario: Gate contention queues within the bridge request budget

- **GIVEN** an HTTP bridge session whose response-create gate is held by a
  legitimate in-flight turn
- **AND** a bridged Responses request that cannot soft-reroute (hard-affinity key
  or `previous_response_id` continuity)
- **WHEN** a gate acquisition attempt exceeds the configured proxy admission wait
  timeout
- **THEN** the request emits capacity-wait keepalive progress on streaming
  surfaces and retries gate acquisition
- **AND** the request completes normally once the in-flight turn releases the
  gate before the bridge request budget expires

#### Scenario: Gate contention still fails once the request budget is exhausted

- **WHEN** a bridged Responses request retries response-create gate acquisition
  until the bridge request budget is exhausted
- **THEN** the request is rejected locally with HTTP 429
- **AND** the error payload uses `error.code = "response_create_gate_timeout"`
- **AND** no response-create gate lease is recorded on that request state

#### Scenario: Soft-affinity requests reroute before waiting

- **GIVEN** a bridged Responses request with a soft-affinity session key and no
  `previous_response_id`
- **WHEN** its first gate acquisition attempt times out
- **THEN** the proxy attempts the internal soft-affinity reroute to a fresh
  bridge session
- **AND** the recoverable gate wait applies only when reroute is not permitted

#### Scenario: Stuck sessions are still detected between attempts

- **WHEN** a gate acquisition attempt times out while a pending bridge request
  has been stuck past the stuck-gate retirement threshold
- **THEN** the stuck session retirement check still runs on that attempt

#### Scenario: In-flight bridge session creation does not finish

- **WHEN** a bridged Responses request waits on another request's in-flight
  session creation marker
- **AND** the in-flight creation does not finish before the configured proxy
  admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and
  `error.code = "capacity_exhausted_active_sessions"` unless the marker's exact
  owner terminates within the additional bounded owner observation
- **AND** a stalled marker without exact owner task provenance is evicted if it
  is still pending

#### Scenario: Exact-owner in-flight bridge session creation resists cancellation

- **WHEN** a bridged Responses request waits on another request's in-flight
  session creation marker that records an exact non-handoff owner task
- **AND** the in-flight creation does not finish before the configured proxy
  admission wait timeout
- **AND** that exact owner remains running through the additional bounded owner
  observation interval
- **THEN** the waiter is rejected locally with HTTP 429 and
  `error.code = "capacity_exhausted_active_sessions"`
- **AND** the owner-held marker remains registered and capacity-owned until that
  owner finalizes

#### Scenario: Bridge capacity waiter does not make progress

- **WHEN** the HTTP bridge is at capacity and a request waits for in-flight
  bridge work to free capacity
- **AND** no capacity becomes available before the configured proxy admission
  wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and
  `error.code = "capacity_exhausted_active_sessions"`

#### Scenario: In-flight owner is cancelled during stale session close

- **WHEN** a bridge session creation owner has published an in-flight marker
- **AND** it is cancelled while closing a stale local bridge session before
  creating the replacement session
- **THEN** the in-flight marker is removed or settled
- **AND** later requests do not remain blocked on that cancelled owner's future

#### Scenario: Exact owner ends after timeout cancellation

- **WHEN** a capacity or same-key admission waiter times out and its exact
  aborted owner terminates within the additional bounded wait
- **THEN** the waiter retries admission if the key is retry-safe
- **AND** no replacement creation begins before the owner terminates

#### Scenario: Exact owner resists cancellation

- **WHEN** a capacity or same-key admission waiter times out and its exact
  aborted owner remains running beyond the additional bounded wait
- **THEN** the waiter returns the existing structured local-overload HTTP 429
- **AND** the owner-held marker remains registered and capacity-owned until that
  owner finalizes

#### Scenario: Capacity waiter does not inherit a retained creator's rejection

- **GIVEN** the HTTP bridge is at capacity because a creator for one key failed
  after its socket came up and is still closing that socket
- **AND** its settled in-flight marker remains registered while the close runs
- **WHEN** a request for an unrelated key waits for capacity
- **THEN** the waiter is not returned the creator's error
- **AND** the waiter retries admission once the creator finalizes, or returns
  HTTP 429 `capacity_exhausted_active_sessions` when the bounded owner
  observation expires first

#### Scenario: Cancelled retained marker does not spin admission

- **GIVEN** a retained in-flight marker was cancelled while its owner is still
  running
- **WHEN** a capacity waiter or a same-key waiter selects that marker
- **THEN** the waiter parks on the owner for at most one bounded observation
  interval instead of re-entering admission repeatedly

#### Scenario: Wedged owner cannot pin a settled marker

- **GIVEN** a settled in-flight marker whose owner is still running
- **WHEN** the marker exceeds the stale in-flight threshold
- **THEN** the stale sweeper reclaims the marker

#### Scenario: Echoed generated turn-state is classified from the recorded alias

- **GIVEN** the origin minted a turn-state for a bridged Responses request and
  recorded it as generated on the session alias
- **WHEN** the client echoes that value on its next request
- **THEN** the bridge session key carries generated provenance
- **AND** a value with no recorded generated alias stays explicit

#### Scenario: Generated turn-state owner is not silently replaced

- **WHEN** a generated turn-state in-flight creation waiter reaches the
  configured proxy admission wait timeout
- **THEN** the waiter returns the existing structured local-overload HTTP 429
- **AND** the owner-held marker remains registered while the owner is still
  running
- **AND** if the owner later finishes creation after registration was aborted,
  the owner closes that unregistered session instead of returning it to the
  caller
