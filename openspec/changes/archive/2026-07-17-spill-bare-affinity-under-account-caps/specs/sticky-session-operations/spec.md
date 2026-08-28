## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed

The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it.

#### Scenario: Soft sticky reallocation uses split primary and secondary pressure thresholds

- **WHEN** a request resolves an existing prompt-cache, sticky-thread, or other explicitly soft mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either the configured primary sticky reallocation threshold or the configured secondary sticky reallocation threshold
- **AND** another eligible account remains at or below both configured sticky reallocation thresholds
- **THEN** selection rebinds the soft sticky-session mapping to the healthier account before sending the request upstream

#### Scenario: Sticky reallocation preserves a pinned account when every candidate is split-threshold pressured

- **WHEN** a request resolves an existing soft sticky-session mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either configured sticky reallocation threshold
- **AND** every other eligible account is also strictly above at least one configured sticky reallocation threshold
- **THEN** selection retains the existing pinned account to avoid sticky-pin thrashing

#### Scenario: Fresh selection does not apply sticky secondary pressure threshold

- **WHEN** a request has no sticky-session mapping
- **AND** one eligible account is above the configured secondary sticky reallocation threshold but below the normal primary budget threshold
- **THEN** the account remains eligible for ordinary non-sticky routing according to the selected routing strategy

#### Scenario: Hard Codex mapping ignores budget-pressure reallocation

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is above a sticky budget-pressure threshold
- **AND** account B has more remaining budget
- **WHEN** the request is selected
- **THEN** selection remains constrained to account A
- **AND** the raw mapping is neither deleted nor rebound to account B

#### Scenario: Unavailable hard Codex owner does not lose its mapping

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is temporarily quota-exceeded or otherwise unusable
- **AND** account B is healthy
- **WHEN** hard-owner selection fails
- **THEN** the request fails closed instead of selecting account B
- **AND** the raw mapping is neither deleted nor rebound

### Requirement: Hard continuity remains owner-bound and bounded

Requests that depend on `previous_response_id`, hard turn-state, nonblank `conversation`, account-scoped `input_file.file_id` pins, live or durable bridge ownership, replay/reattach state, or another required owner continuity source MUST NOT silently reroute to an account that cannot preserve continuity. A resolved required owner MUST override bare process-session locality and MUST be selected without consulting or rewriting that soft mapping. A `previous_response_id` is a stored-object continuation reference and remains owner-bound even when the same request also carries a session header, `prompt_cache_key`, or another soft locality key. If independently resolved hard sources identify different accounts, if live referenced-file pins identify different accounts, or if a request has partial live file-pin coverage, the service MUST fail closed before upstream dispatch. A request for which no referenced file has a live pin MUST preserve opaque `file_id` compatibility and proceed without treating the absent process-local metadata as ownership evidence. If the owner account/session is unavailable or saturated, the service MUST fail closed with an explicit retryable continuity/local overload reason instead of flooding the owner queue indefinitely.

Every HTTP, compact, direct WebSocket, and HTTP-bridge transport MUST resolve explicit turn state against both live and durable bridge aliases. Live, durable, previous-response, file, and explicit turn-state evidence MUST be compared independently; source ordering MUST NOT choose the first match when distinct sessions or accounts resolve. A reused direct WebSocket MUST repeat nonblank `conversation` ownership validation for each response-create frame because the existing socket account proves only the current route. Single-account routing MUST constrain effective routing without narrowing the ownership-candidate pool used by that validation.

When an HTTP-bridge owner is on another replica, the origin MUST forward its resolved file owner in authenticated full-context metadata, and the receiving owner MUST NOT require the same process-local file pin. A retired direct WebSocket's upstream turn-state token MUST NOT be sent to a different account selected for a later movable bare-session request.

A nonblank `conversation` without a dedicated resolved owner MUST proceed only when an explicit hard Codex mapping proves ownership or exactly one account remains in the model/API-key/security-scoped selection pool before transient additional-quota availability, retry exclusions, runtime health, budget, or account-cap filtering. A temporarily quota-filtered, excluded, unhealthy, or capped candidate MUST remain part of this ambiguity check because it may be the actual owner. A bare process-session mapping MUST NOT prove conversation ownership.

#### Scenario: Previous-response owner queue is saturated

- **WHEN** a `/v1/responses` follow-up requires a previous-response owner
- **AND** the owner session queue or account cap is saturated
- **THEN** the service fails closed with `hard_affinity_saturated`, `previous_response_owner_unavailable`, or the applicable stable `account_stream_cap` / `account_response_create_cap` code
- **AND** it does not route to an unrelated account that lacks continuity state

#### Scenario: File-pinned request owner overrides process-session locality

- **GIVEN** a request carries a bare process-session header mapped to account A
- **AND** its `input_file.file_id` is pinned to account B
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
- **AND** at least one file has a live owner pin
- **AND** another file has no live owner pin or the live pins resolve to different accounts
- **WHEN** the request is routed
- **THEN** the service fails with `file_owner_unavailable` or `continuity_owner_conflict`
- **AND** it does not route the files using a soft affinity account

#### Scenario: Opaque file IDs with no live pins preserve compatibility

- **GIVEN** a request references one or more `input_file.file_id` values
- **AND** none of those IDs has a live process-local owner pin
- **WHEN** the request is routed
- **THEN** the service forwards the opaque file references under ordinary unpinned routing
- **AND** it does not invent a hard owner or fail solely because local pin metadata is absent

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

- **GIVEN** a request carries nonblank `conversation` continuity and a file pinned to account B
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

#### Scenario: Remote bridge owner receives file ownership proof

- **GIVEN** an input file is pinned only in origin replica A's process-local index
- **AND** the request's HTTP bridge owner runs on replica B
- **WHEN** replica A forwards the request to replica B
- **THEN** the authenticated forwarding context carries the resolved file owner account
- **AND** replica B accepts that proof without requiring a duplicate local pin
- **AND** a missing, tampered, or legacy-unbound proof is rejected

#### Scenario: Retired WebSocket turn state does not cross accounts

- **GIVEN** a closed upstream WebSocket on account A supplied an account-scoped turn-state token
- **AND** a later movable bare-session frame selects account B
- **WHEN** the proxy opens the replacement WebSocket
- **THEN** it removes account A's stale turn-state token before connect
- **AND** account B never receives that token

## ADDED Requirements

### Requirement: Bare process-session cap spillover is non-mutating

The system MUST distinguish a bare process-level session header from explicit Codex turn-state ownership. It MUST use a storage namespace that normalized request headers cannot occupy, so a client-supplied hard turn-state value cannot alias a derived soft-session row, while legacy raw Codex-session mappings remain hard during rolling upgrades. A current replica MUST consult a legacy raw key even when the namespaced session row also exists, and any raw hit MUST take precedence as hard ownership. If a resolved file, response, or bridge owner conflicts with that raw legacy owner, the request MUST fail closed without creating or rewriting either row.

When the mapped account for a bare process-session key is locally capped and another eligible account is selected, the spillover MUST apply only to that request. Selection MUST NOT update or delete the stored process-session mapping because of account-cap spillover. If the mapped account is below cap, normal sticky selection MUST retain it.

#### Scenario: Capped bare-session owner spills without rebinding

- **GIVEN** a bare process-session mapping points to account A
- **AND** account A is locally capped
- **AND** account B is eligible and below cap
- **WHEN** a self-contained pre-visible request is selected
- **THEN** the request uses account B
- **AND** the stored process-session mapping still points to account A

#### Scenario: Unsaturated bare-session owner retains locality

- **GIVEN** a bare process-session mapping points to eligible account A below its local caps
- **WHEN** a self-contained request is selected
- **THEN** the request uses account A
- **AND** the mapping remains unchanged

#### Scenario: Equal session and turn-state values remain isolated

- **GIVEN** a process-session header and an explicit turn-state header have equal text values
- **WHEN** their affinity mappings are resolved
- **THEN** the process-session mapping uses a source-separated opaque key
- **AND** the explicit turn-state mapping continues to use the legacy raw key as hard ownership

#### Scenario: Derived soft key cannot be reused as raw hard turn state

- **GIVEN** a process-session value has a derived internal storage key
- **WHEN** a client submits the visible representation of that key as a turn-state header
- **THEN** header normalization cannot reproduce the internal storage identity
- **AND** hard turn-state selection cannot read or rewrite the soft row

#### Scenario: Legacy raw mapping remains hard

- **GIVEN** a legacy replica persisted a raw Codex-session mapping
- **WHEN** a current replica receives a bare session header with the same raw value
- **THEN** it does not reinterpret or mutate the legacy raw row as spillable affinity
- **AND** mixed-version operation remains fail-closed for that row

#### Scenario: Coexisting legacy and namespaced rows prefer hard ownership

- **GIVEN** mixed-version replicas created a raw row and a namespaced session row for the same bare session
- **AND** the rows point to different accounts
- **WHEN** a current replica selects the request
- **THEN** the raw row's account is treated as the hard owner
- **AND** neither row is deleted or rewritten by account-cap spillover

#### Scenario: Legacy hard owner conflicts with resolved owner

- **GIVEN** a raw legacy session row points to account A
- **AND** a file, previous response, or bridge resolves to account B
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict`
- **AND** it neither bypasses nor rewrites the raw row
