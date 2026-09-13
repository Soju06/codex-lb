## MODIFIED Requirements

### Requirement: Hard continuity remains owner-bound and bounded

Requests that depend on `previous_response_id`, hard turn-state, nonblank `conversation`, account-scoped `input_file.file_id` pins, live or durable bridge ownership, replay/reattach state, or another required owner continuity source MUST NOT silently reroute to an account that cannot preserve continuity. A resolved required owner MUST override bare process-session locality and MUST be selected without consulting or rewriting that soft mapping. A `previous_response_id` is a stored-object continuation reference and remains owner-bound even when the same request also carries a session header, `prompt_cache_key`, or another soft locality key. If independently resolved hard sources identify different accounts, if live durable referenced-file pins identify different accounts, or if a request has partial live durable file-pin coverage, the service MUST fail closed before upstream dispatch. A request for which no referenced file has a live durable pin MUST preserve opaque `file_id` compatibility and proceed without inventing ownership evidence. If the owner account/session is unavailable or saturated, the service MUST fail closed with an explicit retryable continuity/local overload reason instead of flooding the owner queue indefinitely.

Every HTTP, compact, direct WebSocket, and HTTP-bridge transport MUST resolve explicit turn state against both live and durable bridge aliases. Live, durable, previous-response, file, and explicit turn-state evidence MUST be compared independently; source ordering MUST NOT choose the first match when distinct sessions or accounts resolve. A reused direct WebSocket MUST repeat nonblank `conversation` ownership validation for each response-create frame because the existing socket account proves only the current route. Single-account routing MUST constrain effective routing without narrowing the ownership-candidate pool used by that validation.

When an HTTP-bridge owner is on another replica, the origin MUST forward its resolved durable file owner in authenticated full-context metadata. The receiving owner MUST perform its own fresh shared-database lookup and MUST require that durable result to match the forwarded owner. A missing or conflicting receiver-side durable owner MUST fail closed before account selection or upstream invocation. A retired direct WebSocket's upstream turn-state token MUST NOT be sent to a different account selected for a later movable bare-session request.

A nonblank `conversation` without a dedicated resolved owner MUST proceed only when an explicit hard Codex mapping proves ownership or exactly one account remains in the model/API-key/security-scoped selection pool before transient additional-quota availability, retry exclusions, runtime health, budget, or account-cap filtering. A temporarily quota-filtered, excluded, unhealthy, or capped candidate MUST remain part of this ambiguity check because it may be the actual owner. A bare process-session mapping MUST NOT prove conversation ownership.

#### Scenario: Previous-response owner queue is saturated

- **WHEN** a `/v1/responses` follow-up requires a previous-response owner
- **AND** the owner session queue or account cap is saturated
- **THEN** the service fails closed with `hard_affinity_saturated`, `previous_response_owner_unavailable`, or the applicable stable `account_stream_cap` / `account_response_create_cap` code
- **AND** it does not route to an unrelated account that lacks continuity state

#### Scenario: File-pinned request owner is capped

- **WHEN** a `/v1/responses` request references an `input_file.file_id` pinned to an owner account
- **AND** the owner account is at its account stream or response-create cap
- **THEN** the service returns a local account-cap overload for the owner
- **AND** it does not route the file reference to another account

#### Scenario: File-pinned request owner overrides process-session locality

- **GIVEN** a request carries a bare process-session header mapped to account A
- **AND** its `input_file.file_id` is durably pinned to account B
- **WHEN** the request is routed
- **THEN** account B is treated as the required owner
- **AND** the process-session mapping is neither consulted as an owner nor rewritten

#### Scenario: Conflicting hard owners fail closed

- **GIVEN** a turn state, previous response, bridge, or input file resolves to account A
- **AND** another hard source on the same request resolves to account B
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict` before upstream dispatch
- **AND** source ordering does not choose either owner

#### Scenario: Partial or cross-account file pins fail closed

- **GIVEN** a request references multiple account-scoped input files
- **AND** at least one file has a live durable owner pin
- **AND** another file has no live durable owner pin or the live pins resolve to different accounts
- **WHEN** the request is routed
- **THEN** the service fails with `file_owner_unavailable` or `continuity_owner_conflict`
- **AND** it does not route the files using a soft affinity account

#### Scenario: Opaque file IDs with no live durable pins preserve compatibility

- **GIVEN** a request references one or more `input_file.file_id` values
- **AND** none of those IDs has a live durable owner pin
- **WHEN** the request is routed
- **THEN** the service forwards the opaque file references under ordinary unpinned routing
- **AND** it does not invent a hard owner or fail solely because durable pin metadata is absent

#### Scenario: Ambiguous conversation fails closed

- **GIVEN** a request carries nonblank `conversation` continuity and only bare process-session affinity
- **AND** more than one account is eligible
- **WHEN** no dedicated or hard-mapping owner can be resolved
- **THEN** the request fails with a stable owner-unavailable error before upstream dispatch

#### Scenario: Account-cap pressure does not manufacture a conversation owner

- **GIVEN** two accounts remain in the model/API-key/security-scoped selection pool
- **AND** one account is temporarily at its local account cap
- **WHEN** a request carries nonblank `conversation` continuity without a dedicated or hard-mapping owner
- **THEN** the request still fails with a stable owner-unavailable error
- **AND** the uncapped account is not treated as the unique owner

#### Scenario: Retry or additional-quota filtering does not manufacture a conversation owner

- **GIVEN** two accounts remain in the model/API-key/security-scoped selection pool
- **AND** retry exclusion or transient additional-quota availability removes one from the effective routing pool
- **WHEN** a request carries nonblank `conversation` continuity without a dedicated or hard-mapping owner
- **THEN** the request still fails with a stable owner-unavailable error
- **AND** the remaining effective account is not treated as the unique owner

#### Scenario: Account status does not manufacture a conversation owner

- **GIVEN** two accounts are in the model/API-key/security ownership pool
- **AND** one account is paused, requires reauthentication, deactivated, or otherwise unavailable for routing
- **WHEN** a request carries nonblank `conversation` continuity without a dedicated or hard-mapping owner
- **THEN** the request still fails with a stable owner-unavailable error
- **AND** the active account is not treated as the unique owner

#### Scenario: Preferred file owner does not manufacture a conversation owner

- **GIVEN** a request carries nonblank `conversation` continuity and a file durably pinned to account B
- **AND** another account remains in the model/API-key/security ownership pool
- **WHEN** no dedicated conversation owner can be resolved
- **THEN** file ownership does not narrow the conversation ambiguity check to account B
- **AND** the request fails closed before upstream dispatch

#### Scenario: Bridge turn state is owner-bound across transports

- **GIVEN** an HTTP bridge registered a turn-state alias for account A
- **WHEN** the alias is reused through compact, plain HTTP streaming, or direct WebSocket transport
- **THEN** each transport treats account A as the required owner
- **AND** it does not fall back to unrelated sticky affinity

#### Scenario: Independent bridge aliases conflict

- **GIVEN** a live or durable turn-state alias resolves to one bridge session
- **AND** a previous-response alias on the same request resolves to a distinct session or account
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict`
- **AND** alias lookup order does not select either session

#### Scenario: Reused WebSocket revalidates conversation ownership

- **GIVEN** a direct upstream WebSocket is already open on account A
- **AND** a later response-create frame carries nonblank `conversation`
- **WHEN** more than one account remains in the ownership-candidate pool
- **THEN** the later frame fails with a stable owner-unavailable error before upstream send
- **AND** the existing socket account is not treated as ownership proof

#### Scenario: Single-account routing does not manufacture conversation ownership

- **GIVEN** single-account routing selects account A
- **AND** multiple accounts remain in the model/API-key/security ownership pool
- **WHEN** a request carries nonblank `conversation` without dedicated owner evidence
- **THEN** the request remains ambiguous and fails closed
- **AND** only the effective routing states are constrained to account A

#### Scenario: Remote bridge owner revalidates forwarded file ownership

- **GIVEN** origin replica A durably resolves an input file to account A
- **AND** the request's HTTP bridge owner runs on replica B
- **WHEN** replica A forwards the request to replica B with authenticated file-owner metadata
- **THEN** replica B MUST freshly resolve the shared durable pin
- **AND** it MUST accept the forwarded owner only when both owner values match
- **AND** a missing, conflicting, tampered, or legacy-unbound proof MUST be rejected before upstream invocation

#### Scenario: Retired WebSocket turn state does not cross accounts

- **GIVEN** a closed upstream WebSocket on account A supplied an account-scoped turn-state token
- **AND** a later movable bare-session frame selects account B
- **WHEN** the proxy opens the replacement WebSocket
- **THEN** it removes account A's stale turn-state token before connect
- **AND** account B never receives that token
