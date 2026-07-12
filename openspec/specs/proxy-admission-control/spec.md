# proxy-admission-control Specification

## Purpose
Define how the proxy protects itself under load while preserving short request paths and surfacing local overload clearly.
## Requirements
### Requirement: Downstream proxy admission is split by traffic class

The system MUST enforce independent downstream admission limits for proxy HTTP requests, proxy websocket sessions, compact HTTP requests, and dashboard traffic. Exhausting one proxy lane MUST NOT consume capacity from the others.

#### Scenario: Websocket session load does not starve HTTP responses
- **WHEN** the proxy websocket admission lane is full
- **THEN** new websocket sessions are rejected locally
- **AND** eligible proxy HTTP requests may still proceed if their own lane has capacity

#### Scenario: Compact lane survives general proxy load
- **WHEN** the general proxy HTTP lane is saturated
- **AND** the compact lane still has capacity
- **THEN** `/backend-api/codex/responses/compact` and `/v1/responses/compact` requests continue to be admitted

### Requirement: Local overload responses are explicit

When the proxy rejects a request locally because an admission lane or expensive-work stage is full, it MUST return a local-overload response with a `Retry-After` header. HTTP requests MUST use an OpenAI-style error envelope and websocket handshake denials MUST use an HTTP denial response instead of a pre-accept close frame.

#### Scenario: HTTP admission rejection returns explicit overload envelope
- **WHEN** a proxy HTTP request is rejected locally for overload
- **THEN** the response status is `429`
- **AND** the response includes `Retry-After`
- **AND** the error payload identifies the failure as local proxy overload instead of upstream unavailability

#### Scenario: Websocket handshake rejection returns explicit overload status
- **WHEN** a websocket handshake is rejected locally for overload
- **THEN** the client receives an HTTP denial response with the real overload status
- **AND** the server access log reflects that overload status instead of `403 Forbidden`

### Requirement: Expensive upstream work is admission controlled

The proxy MUST enforce separate in-process admission limits for token refresh, upstream websocket connect, and first-turn response creation.

#### Scenario: Owner-switch blocked websocket releases response-create admission

- **GIVEN** a websocket request has acquired response-create admission
- **AND** the request cannot switch to its required previous-response owner because another request is still streaming on the current upstream socket
- **WHEN** the proxy emits `previous_response_owner_unavailable` for the blocked request
- **THEN** it releases that request's response-create gate and account response-create lease
- **AND** later eligible requests are not blocked by stale local response-create pressure

### Requirement: Account-local Responses work is capped before upstream creation

For `/v1/responses`, `/backend-api/codex/responses`, and compact Responses traffic, the proxy MUST enforce account-local response-create and streaming concurrency limits in addition to process-wide admission limits. The default account response-create cap MUST be 4 and the default account stream cap MUST be 8 unless operators configure a different value. When an account is at either cap, new soft-affinity work MUST prefer another eligible account before returning local overload. Hard-continuity work MAY fail closed when the required owner account is saturated.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a soft-affinity `/v1/responses` request is routed
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner account
- **AND** that account is at its account stream or response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality

### Requirement: Local overload reasons are stable and distinguishable

Local Responses overload failures MUST expose stable low-cardinality reason fields in logs and metrics so operators can distinguish `bridge_queue_full`, `response_create_gate_timeout`, `hard_affinity_saturated`, `previous_response_owner_unavailable`, `global_admission_timeout`, `capacity_exhausted_active_sessions`, `account_response_create_cap`, and `account_stream_cap`. These local reasons MUST NOT be reported as upstream rate limits or generic upstream unavailability.

#### Scenario: Bridge queue saturation is not ambiguous

- **WHEN** a local HTTP bridge queue rejects a request
- **THEN** logs and metrics use the stable reason `bridge_queue_full`
- **AND** they do not use the ambiguous alias `queue_full`

#### Scenario: Account cap rejection is local overload

- **WHEN** every eligible account is unavailable because of account-local caps
- **THEN** the HTTP response is a local overload response with `Retry-After`
- **AND** logs and metrics identify `account_response_create_cap` or `account_stream_cap`
- **AND** the error message identifies the exhausted account-local cap instead of saying all upstream accounts are unavailable

### Requirement: HTTP bridge startup admission waits are bounded

The proxy MUST apply the configured proxy admission wait timeout to HTTP bridge startup waits for per-session response-create gate acquisition, bridge capacity waiters, and in-flight session creation waiters. When the timeout expires, the proxy MUST reject the request locally with HTTP 429 and an OpenAI-style `proxy_overloaded` error envelope. Timing out while observing another request's pending in-flight session creation MUST evict that in-flight marker when it is still pending so later requests can attempt a fresh bridge session instead of waiting on the same stalled future.

If a request owns in-flight bridge session creation and is cancelled or fails after publishing the in-flight marker but before registering the created session, the proxy MUST remove or settle that in-flight marker. If a session owner later finishes creation after its in-flight marker was evicted, the owner MUST NOT return an unregistered bridge session to the caller.

#### Scenario: Per-session response-create gate does not open

- **WHEN** a bridged Responses request waits for a session response-create gate
- **AND** the gate does not open before the configured proxy admission wait timeout
- **THEN** the request is rejected locally with HTTP 429
- **AND** the error payload uses `error.code = "proxy_overloaded"`
- **AND** no response-create gate lease is recorded on that request state

#### Scenario: In-flight bridge session creation does not finish

- **WHEN** a bridged Responses request waits on another request's in-flight session creation
- **AND** the in-flight creation does not finish before the configured proxy admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`
- **AND** the stalled in-flight marker is evicted if it is still pending

#### Scenario: Bridge capacity waiter does not make progress

- **WHEN** the HTTP bridge is at capacity and a request waits for in-flight bridge work to free capacity
- **AND** no capacity becomes available before the configured proxy admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`

#### Scenario: In-flight owner is cancelled during stale session close

- **WHEN** a bridge session creation owner has published an in-flight marker
- **AND** it is cancelled while closing a stale local bridge session before creating the replacement session
- **THEN** the in-flight marker is removed or settled
- **AND** later requests do not remain blocked on that cancelled owner's future

### Requirement: Opportunistic Proxy Traffic Burns Only Safe Quota

When a proxy request is authenticated by an API key whose `traffic_class` is `opportunistic`, the proxy SHALL admit the request only if at least one eligible account can serve opportunistic traffic without crossing the routing policy floors.

Burn-first and normal accounts MAY be drained to zero only when another usable foreground account remains. The last usable normal account SHALL keep an emergency reserve. Preserve accounts SHALL require fresh usage data and SHALL remain above dynamic weekly and 5h floors.

#### Scenario: Closed burn window returns OpenAI rate limit
- **WHEN** an opportunistic API key calls a protected Codex-compatible route and no account is currently burnable
- **THEN** the proxy returns HTTP `429`
- **AND** the response uses an OpenAI-style error with code `rate_limit_exceeded`
- **AND** the message begins with `opportunistic burn window closed:`
- **AND** the response includes `Retry-After`

#### Scenario: Preflight admission mirrors routing
- **WHEN** an opportunistic API key calls `/backend-api/codex/opportunistic/admission`
- **THEN** the proxy returns `200` only when the same traffic class could select an account for a real request
- **AND** otherwise returns the same OpenAI-style `429` denial shape

### Requirement: Additional Quota Routing Policies Inherit Or Override Account Policy

When a model is mapped to an additional quota, the proxy SHALL use fresh additional-quota availability as the routing gate and SHALL NOT reject an account solely because its standard 5h or 7d Codex quota is exhausted.

Additional quota routing policy `inherit` SHALL use the selected account's routing policy. Additional quota routing policies `burn_first`, `normal`, and `preserve` SHALL override account routing policy for requests gated by that additional quota.

The dashboard SHALL expose the configured routing policy for each known additional quota and allow operators to switch between `inherit`, `burn_first`, `normal`, and `preserve`.

#### Scenario: Spark can burn its separate pool
- **GIVEN** an account has fresh available `codex_spark` additional quota
- **AND** the account's standard Codex quota is exhausted
- **WHEN** a request selects `gpt-5.3-codex-spark`
- **THEN** the proxy MAY select that account

### Requirement: Stuck HTTP bridge response-create gate sessions are retired
When a visible HTTP bridge request times out waiting for a per-session response-create gate, the proxy MUST retire the bridge session only if pending visible request age meets or exceeds the configured stuck-gate retirement threshold. The retirement MUST emit a structured low-cardinality log and a Prometheus counter without raw keys or prompt content.

#### Scenario: Old pending work blocks a visible gate waiter
- **WHEN** a visible HTTP bridge request receives `response_create_gate_timeout`
- **AND** at least one visible pending request on the same session is older than the configured stuck-gate retirement threshold
- **THEN** the proxy retires the bridge session so later requests can create a fresh session
- **AND** the waiter is rejected cleanly with `response_create_gate_timeout`

#### Scenario: Healthy active stream is not retired during a normal wait
- **WHEN** a visible HTTP bridge request times out waiting for the gate
- **AND** the session has no pending visible request older than the configured stuck-gate retirement threshold
- **THEN** the proxy rejects only the waiter
- **AND** the bridge session remains available for the existing in-flight request

### Requirement: Account stream capacity reserves recovery headroom

The proxy MUST reserve the configured number of account-local stream slots from ordinary first-turn and follow-up selection, while allowing reattach work to use the full account stream cap. The default recovery reserve MUST be one slot. The reserve MUST NOT increase the configured hard stream cap.

#### Scenario: Fan-out leaves one slot for reattach

- **GIVEN** an account stream cap of eight and a recovery reserve of one
- **AND** seven ordinary streams are active
- **WHEN** another ordinary stream and a reattach stream compete for capacity
- **THEN** the ordinary stream receives local account-cap backpressure
- **AND** the reattach stream may acquire the eighth slot

### Requirement: Dashboard-configurable account concurrency caps

The dashboard settings API MUST persist nonnegative per-account `proxy_account_response_create_limit`, `proxy_account_stream_limit`, and `proxy_account_stream_recovery_reserve` overrides. A settings row created for the first time MUST persist the process environment values for those settings. Existing settings rows upgraded to this capability MUST use nullable overrides so a NULL value continues to inherit the corresponding process environment value until explicitly changed by an operator.

#### Scenario: Operator changes caps without restart

- **GIVEN** the dashboard cache contains persisted account concurrency caps
- **WHEN** an operator updates one or more cap values through `PUT /api/settings`
- **THEN** the response returns the persisted values
- **AND** subsequent new selection and lease decisions use the updated cached values without mutating global process settings

#### Scenario: Negative cap is rejected

- **WHEN** an operator supplies a negative account concurrency cap or recovery reserve
- **THEN** the settings API rejects the request
- **AND** the previously persisted values remain unchanged

#### Scenario: Operator edits caps in the dashboard

- **GIVEN** an operator opens routing settings
- **WHEN** the operator enters nonnegative integer cap values and saves them
- **THEN** the dashboard sends all three values through the settings API
- **AND** `0` is presented as unlimited
- **AND** a bounded stream recovery reserve greater than the stream cap is rejected before saving

### Requirement: Cached caps govern runtime admission

New account selection, account lease acquisition, opportunistic admission, and account-cap error reporting MUST use one dashboard-settings cache snapshot obtained before entering runtime locks. These paths MUST NOT read the database or await the dashboard settings cache while holding a runtime lock.

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment stream cap differs from the persisted dashboard stream cap
- **WHEN** a new stream selection or lease acquisition occurs
- **THEN** the persisted cached dashboard cap controls the decision

### Requirement: Stream recovery reserve remains a selection reserve

The configured stream recovery reserve MUST remain a subtractive reserve for ordinary stream selection. Recovery selection without an ordinary reserve MAY use the full stream cap. A nonpositive stream cap continues to mean unlimited streams.

#### Scenario: Recovery may use a reserved slot

- **GIVEN** ordinary stream selection has consumed the configured ordinary capacity
- **WHEN** recovery stream selection is attempted without an ordinary reserve
- **THEN** it may acquire a remaining slot up to the configured stream cap

