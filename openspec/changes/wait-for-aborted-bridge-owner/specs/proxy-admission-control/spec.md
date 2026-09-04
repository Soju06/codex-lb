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
this retry path to replace generated turn-state in-flight creation; generated
turn-state creator timeouts keep the existing structured local-overload HTTP
429 behavior and the late owner MUST NOT return an unregistered bridge session
to the caller.

If a request owns in-flight bridge session creation and is cancelled or fails
after publishing the in-flight marker but before registering the created
session, the proxy MUST remove or settle that in-flight marker. If a session
owner later finishes creation after its in-flight marker was evicted, the owner
MUST NOT return an unregistered bridge session to the caller.

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
  session creation
- **AND** the in-flight creation does not finish before the configured proxy
  admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and
  `error.code = "capacity_exhausted_active_sessions"`
- **AND** the stalled in-flight marker is evicted if it is still pending

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

#### Scenario: Generated turn-state owner is not silently replaced

- **WHEN** a generated turn-state in-flight creation waiter reaches the
  configured proxy admission wait timeout
- **THEN** the waiter returns the existing structured local-overload HTTP 429
- **AND** the owner-held marker remains registered while the owner is still
  running
- **AND** if the owner later finishes creation after registration was aborted,
  the owner closes that unregistered session instead of returning it to the
  caller
