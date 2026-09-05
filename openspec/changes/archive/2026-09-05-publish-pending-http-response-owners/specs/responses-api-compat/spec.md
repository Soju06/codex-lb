## MODIFIED Requirements

### Requirement: Observed HTTP response IDs publish same-process ownership before delivery

When an HTTP Responses attempt extracts a valid response ID from an actual upstream lifecycle event, it MUST publish that ID to the existing bounded process owner cache with the selected account and existing API-key/session scope before delivering the event that exposes the ID downstream. An immediate same-process follow-up referencing that ID MUST be able to resolve its known owner without waiting for the originating request-log write or originating stream completion. This readiness MUST apply from the first observed lifecycle event carrying the ID, including `response.created`, `response.queued`, and `response.in_progress`, whether delivered as SSE or adapted from a canonical background JSON acknowledgement; it MUST NOT promise that an unfinished response is already usable by the upstream provider.

The service MUST NOT publish a locally generated request/synthetic-error ID or a client-supplied anchor as new upstream ownership evidence. Cache misses MUST retain the existing durable request-log lookup and genuinely unknown-owner fail-closed behavior. Request-log persistence MUST remain under its existing detached task owner; this requirement MUST NOT introduce synchronous log barriers, a new registry or a cross-replica readiness guarantee.

Provenance for locally generated terminals MUST remain internal to the SSE carrier, preserve the exact serialized event bytes and existing retry markers, and survive reattachment of the parsed payload.

When normalization of an actual upstream error supplies a local response ID, that ID MUST remain ineligible for early ownership publication. The event MUST retain its upstream origin for timing observations.

#### Scenario: Follow-up starts after response-created delivery
- **GIVEN** two eligible accounts and an HTTP stream that has exposed its upstream response ID in `response.created` but has not completed
- **WHEN** a same-process HTTP follow-up references that ID
- **THEN** the known selected account is resolved before upstream dispatch
- **AND** ownership resolution does not wait for the first stream's terminal event or request-log write

#### Scenario: Terminal follow-up races detached persistence
- **GIVEN** a successful HTTP response whose request-log persistence is still pending
- **WHEN** the client submits an anchored follow-up immediately after terminal delivery or EOF
- **THEN** the existing process cache resolves the response owner in the existing caller scope
- **AND** the request is not rejected as unknown-owner solely because that write is pending

#### Scenario: Unobserved and out-of-scope IDs do not gain ownership
- **WHEN** a request references an ID not authoritatively observed for its allowed owner scope, including a local synthetic ID
- **THEN** no new cache entry is inferred from that request
- **AND** existing durable lookup, authorization and unknown-owner fail-closed rules apply

#### Scenario: Background acknowledgement precedes its log
- **GIVEN** two eligible accounts and a canonical HTTP background JSON acknowledgement with status `queued` or `in_progress`
- **WHEN** the same caller submits a continuation after receiving the acknowledgement while its log is pending
- **THEN** the known owner MUST resolve and receive that continuation without waiting for the originating log
- **AND** the acknowledgement MUST preserve its upstream ID and status

#### Scenario: In-progress lifecycle follows token delivery
- **GIVEN** an HTTP stream has delivered a text delta and first exposes its authoritative response ID in `response.in_progress`
- **WHEN** the event reaches the caller before stream completion
- **THEN** a same-process continuation MUST resolve the known owner before upstream dispatch
