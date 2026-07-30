## MODIFIED Requirements

### Requirement: Account-local Responses work is capped before upstream creation

For `/v1/responses`, `/backend-api/codex/responses`, and compact Responses traffic, the proxy MUST enforce account-local response-create and streaming concurrency limits in addition to process-wide admission limits, and the configured limits MUST be cluster-wide per-account targets enforced across all replicas rather than per-replica allowances. Because per-account caps are partitioned per replica via the bridge ring and cannot be safely partitioned across intra-pod worker processes, each instance MUST run a single worker process; horizontal scaling is achieved by adding replicas. The default account response-create cap MUST be 4 and the default account stream cap MUST be 8 unless operators configure a different value.

When an account is at either cap, new soft-affinity work MUST prefer another eligible account before returning local overload. A bare process-session mapping MAY supply soft locality only while the request is self-contained, pre-visible, and has no required owner. Account-cap spillover MUST be decided during account selection and MUST NOT switch an account after a request enters shared transport, replay, or durable bridge ownership. Hard-continuity work MUST remain on its required owner and MAY fail closed when that owner is saturated. Hard Codex ownership rows MUST bypass soft sticky fallback/reallocation so pressure cannot delete or rewrite them.

The recoverable account-capacity wait entered by bridged Responses session creation and recovery when every eligible account is capped MUST be bounded by a fixed 120-second ceiling in addition to the bridge request budget, anchored per request at the first local-capacity wait. The original local-cap error and deadline MUST remain active across request-state re-preparation and subsequent recoverable waits, except that per-session response-create gate waits (`response_create_gate_timeout`) MUST remain bounded by the bridge request budget only. When the bound expires, the proxy MUST surface the original HTTP 429 local-cap error envelope (`account_stream_cap` or `account_response_create_cap`) and MUST record the terminal failure in the request log with the local-cap error code and `account_capacity_wait` failure phase.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a self-contained `/v1/responses` request has only bare process-session affinity to account A
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner account
- **AND** that account is at its account stream or response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality

#### Scenario: Late WebSocket cap race does not retire shared work

- **GIVEN** a request has entered an upstream WebSocket shared with another in-flight response
- **WHEN** a later account response-create lease acquisition loses a capacity race
- **THEN** the proxy rejects only the newly unadmitted request with the existing local-cap failure
- **AND** it does not retire or switch the shared upstream WebSocket to spill that request

#### Scenario: Existing bridge ownership is not replaced by cap spillover

- **GIVEN** a session header resolves to a live or durable HTTP bridge owner
- **WHEN** that owner's account or response-create gate is saturated
- **THEN** the request follows the existing hard bridge-capacity behavior
- **AND** account-cap spillover does not publish a replacement bridge under the same canonical identity

#### Scenario: Account-capacity wait surfaces the cap error at the ceiling

- **GIVEN** every eligible account for a bridged Responses request stays at its stream cap
- **WHEN** the recoverable account-capacity wait exceeds the 120-second ceiling
- **THEN** the request fails with HTTP 429 and `error.code = "account_stream_cap"`
- **AND** the connection is still open to deliver the error envelope
- **AND** a request-log row records the failure with `error.code = "account_stream_cap"` and `failure_phase = "account_capacity_wait"`

#### Scenario: Gate contention is not bounded by the capacity ceiling

- **GIVEN** a bridged Responses request waiting on a per-session response-create gate held by an in-flight turn
- **WHEN** the wait exceeds the 120-second account-capacity ceiling
- **THEN** the gate wait continues, bounded by the bridge request budget only
