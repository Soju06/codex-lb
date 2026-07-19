# sticky-session-operations Specification

## Purpose

Define sticky-session operation contracts so durable sessions, dashboard affinity, and prompt-cache affinity stay distinct.
## Requirements
### Requirement: Sticky sessions are explicitly typed
The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it.

#### Scenario: Soft sticky reallocation uses split primary and secondary pressure thresholds
- **WHEN** a request resolves an existing prompt-cache, sticky-thread, or other explicitly soft mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either the configured primary sticky reallocation threshold or the configured secondary sticky reallocation threshold
- **AND** another eligible account remains at or below both configured sticky reallocation thresholds
- **THEN** selection rebinds the sticky-session mapping to the healthier account before sending the request upstream

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

### Requirement: Dashboard exposes sticky-session administration
The system SHALL provide dashboard APIs for listing sticky-session mappings, deleting one mapping, and purging stale mappings.

#### Scenario: List sticky-session mappings
- **WHEN** the dashboard requests sticky-session entries
- **THEN** the response includes each mapping's `key`, `account_id`, `kind`, `created_at`, `updated_at`, `expires_at`, and `is_stale`
- **AND** the response includes the total number of stale `prompt_cache` mappings that currently exist beyond the returned page

#### Scenario: List only stale mappings
- **WHEN** the dashboard requests sticky-session entries with `staleOnly=true`
- **THEN** the system applies stale prompt-cache filtering before enforcing the result limit

#### Scenario: Delete one mapping
- **WHEN** the dashboard deletes a sticky-session mapping by both `key` and `kind`
- **THEN** the system removes that mapping and returns a success response

#### Scenario: Purge stale prompt-cache mappings
- **WHEN** the dashboard requests a stale purge
- **THEN** the system deletes only stale `prompt_cache` mappings and leaves durable mappings untouched

### Requirement: Prompt-cache mappings are cleaned up proactively
The system SHALL run a background cleanup loop that deletes stale `prompt_cache` mappings using the current dashboard prompt-cache affinity TTL.

#### Scenario: Cleanup loop removes stale prompt-cache mappings
- **WHEN** the cleanup loop runs and finds `prompt_cache` mappings older than the configured TTL
- **THEN** it deletes those mappings

#### Scenario: Cleanup loop preserves durable mappings
- **WHEN** the cleanup loop runs
- **THEN** it does not delete `codex_session` or `sticky_thread` mappings regardless of age

### Requirement: Soft bridge affinity can reroute under local pressure

Prompt-cache and sticky-thread bridge affinity that does not carry a hard continuity dependency MUST be treated as soft. A client-supplied or proxy-derived `prompt_cache_key` is a cache-locality hint, not a correctness dependency; the proxy MAY reroute it under local pressure and accept lower cache-hit rates. When the preferred soft bridge session is saturated by queue depth, response-create gate pressure, bridge capacity, or account-local caps, the service MUST evaluate other eligible accounts/sessions before returning a local overload response. The service MUST emit internal diagnostics such as `internal_soft_affinity_reroute` for successful reroutes without adding those diagnostic names to the stable failure taxonomy.

#### Scenario: Prompt-cache bridge queue reroutes to an eligible account

- **GIVEN** a prompt-cache request's preferred bridge session queue is full
- **AND** another eligible account/session is below cap
- **WHEN** the request has no hard previous-response or turn-state continuity dependency
- **THEN** the proxy routes to the alternate account/session
- **AND** records an internal soft-affinity reroute diagnostic

#### Scenario: Prompt cache key does not override hard previous-response continuity

- **GIVEN** a `/v1/responses` request carries both `previous_response_id` and `prompt_cache_key`
- **AND** the previous response owner is known
- **WHEN** the prompt-cache preferred account differs from the previous-response owner
- **THEN** the proxy treats the request as hard owner-bound to the previous-response owner
- **AND** it does not route to the prompt-cache account when that account cannot preserve the stored response continuation

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

#### Scenario: File-pinned request owner is capped

- **WHEN** a `/v1/responses` request references an `input_file.file_id` pinned to an owner account
- **AND** the owner account is at its account stream or response-create cap
- **THEN** the service returns a local account-cap overload for the owner
- **AND** it does not route the file reference to another account

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

### Requirement: Hard HTTP bridge reconnects remain account-bound after upstream close

When an HTTP responses bridge session uses a hard continuity key such as `turn_state_header` or `session_header`, replay or reconnect handling MUST NOT route the same pending request to a different upstream account solely because the prior upstream WebSocket closed with code `1011`.

Soft-affinity bridge sessions MAY continue to exclude the failed account for transient upstream close recovery when no hard continuity dependency is present.

#### Scenario: session-header bridge replay preserves owner account after 1011

- **GIVEN** an HTTP bridge session is keyed by `session_header`
- **AND** its upstream WebSocket closes with code `1011` before `response.completed`
- **WHEN** the bridge attempts a pre-created replay or reconnect for the pending request
- **THEN** the account selector is called with the current session account as the preferred account
- **AND** the current session account is not excluded solely because of the `1011` close
- **AND** the request is not replayed on another account unless an explicit non-1011 account-failure path requires it

### Requirement: HTTP bridge upstream WebSocket connects use WebSocket-safe headers

When HTTP responses bridge code opens or reconnects an upstream responses WebSocket, it MUST remove HTTP-only and hop-by-hop inbound headers before passing headers to the upstream WebSocket connector.

The upstream responses WebSocket header builder MUST NOT forward HTTP Responses API beta tokens such as `responses=experimental`; it MUST send the responses WebSocket beta token required by the upstream WebSocket protocol.

The sanitized header set MUST preserve Codex continuity headers such as `session_id`, `x-codex-session-id`, and `x-codex-turn-state` when those headers are required for affinity.

#### Scenario: HTTP bridge create filters HTTP request headers

- **GIVEN** an HTTP responses bridge request contains HTTP request headers such as `accept`, `accept-encoding`, `content-type`, `connection`, `authorization`, `cookie`, or `host`
- **WHEN** the bridge opens a new upstream responses WebSocket
- **THEN** those HTTP-only or hop-by-hop headers are not forwarded to the upstream WebSocket connector
- **AND** the continuity `session_id` header remains available for upstream affinity

#### Scenario: HTTP bridge reconnect filters HTTP request headers

- **GIVEN** an HTTP responses bridge session is reconnecting an upstream responses WebSocket
- **AND** the session stores HTTP request headers from the original downstream request
- **WHEN** reconnect prepares the upstream WebSocket headers
- **THEN** HTTP-only and hop-by-hop headers are filtered before the upstream WebSocket connector is called
- **AND** the selected `x-codex-turn-state` remains available for upstream continuity

#### Scenario: upstream WebSocket beta header excludes HTTP Responses token

- **GIVEN** a responses WebSocket connect request receives `OpenAI-Beta: responses=experimental`
- **WHEN** upstream WebSocket headers are built
- **THEN** `responses=experimental` is not forwarded
- **AND** `responses_websockets=2026-02-06` is present

### Requirement: Unanchored process-session concurrency uses independent bridge lanes

When multiple Responses requests share a process-level session header but carry neither `previous_response_id` nor non-blank turn-state continuity, the service MUST NOT queue an independent request behind an active response-create gate. If the canonical bridge is still being created, reserved by another request before submit, already has a visible request, or belongs to a different model class, the service MUST create a server request-scoped bridge lane. The lane identity MUST NOT depend on a client-controlled request ID. The fork MUST leave the canonical bridge and its model metadata unchanged. When such requests carry an explicit `prompt_cache_key`, the stable bridge identity MUST combine it with the process-level session header so distinct Codex agent threads remain isolated even when they execute sequentially; repeated requests from the same thread MUST retain one identity. Requests without an explicit prompt-cache key MUST retain the legacy session-header identity. A pre-submit handoff reservation MUST protect its bridge from idle pruning and capacity eviction, and any cancellation or error between lookup and visible submission MUST release it. Owner forwarding MUST preserve whether a session-header or internal-fork request was unanchored instead of treating a proxy-generated downstream turn-state as an explicit client anchor, but MUST NOT attach that v2-only state to prompt-cache or unrelated affinity families. It MUST fail closed when a mixed-version hop cannot authenticate required unanchored state. The v2 primary signature MUST bind whether client-IP metadata was present, while the companion signature MUST bind its value. When the canonical owner itself creates a fork for a forwarded request, it MUST own that fork locally instead of re-hashing it into another forwarding hop. Explicitly anchored owner forwards MUST retain the legacy-compatible primary signature during rolling upgrades, and a receiving instance MUST reject ambiguous delimiter-bearing legacy fields. Durable aliases derived from the forked lane MUST retain hard owner and account continuity. If durable ownership fencing rejects a stale owner's new alias, the stale owner MUST remove the matching local alias without removing a newer local generation's mapping.

#### Scenario: sequential child agent does not reuse parent bridge history

- **GIVEN** a parent and child Codex agent share one process session header
- **AND** each agent supplies its own stable explicit `prompt_cache_key`
- **WHEN** the child starts after the parent's visible request has completed
- **THEN** the child uses a different bridge identity from the parent
- **AND** another request from that same child keeps the child's bridge identity

#### Scenario: Background requests do not block behind a foreground turn

- **GIVEN** a foreground request is active on a session-header bridge
- **WHEN** two unanchored background requests arrive with the same session header
- **THEN** each background request uses an independent response-create gate
- **AND** neither request waits for the foreground response to complete
- **AND** the foreground bridge's model metadata remains unchanged

#### Scenario: Lookup-to-submit requests remain isolated

- **GIVEN** an unanchored request has reserved an idle canonical bridge but has not yet made queued activity visible
- **WHEN** another unanchored request arrives with the same session header and client request ID
- **THEN** the second request uses a distinct server-scoped bridge lane
- **AND** it does not reuse the reserved canonical bridge

#### Scenario: Durable refresh publishes the handoff reservation

- **GIVEN** an unanchored request reuses an idle durable canonical bridge
- **WHEN** refreshing the durable lease yields before lookup returns
- **THEN** the canonical bridge is already reserved for that request
- **AND** a concurrent unanchored request uses a distinct server-scoped lane

#### Scenario: Cancelled pre-submit handoff does not strand a reservation

- **GIVEN** an unanchored request is reusing an idle canonical bridge
- **WHEN** the request is cancelled after claiming the bridge but before queued activity becomes visible
- **THEN** the canonical bridge remains unreserved
- **AND** later requests are not forced onto fork lanes by the cancelled lookup

#### Scenario: Payload preparation failure does not strand a reservation

- **GIVEN** an unanchored request has reserved an idle canonical bridge
- **WHEN** anchor injection, trimming, or payload validation fails before submission
- **THEN** request-scope cleanup releases the reservation
- **AND** later requests may reuse the canonical bridge

#### Scenario: Remote owner preserves unanchored concurrency

- **GIVEN** an unanchored request is forwarded to the canonical bridge owner
- **AND** the proxy generated a downstream turn-state for response aliasing
- **WHEN** the owner receives the forwarded request while the canonical lane is active
- **THEN** the owner still treats the request as unanchored
- **AND** the request uses an independent bridge lane
- **AND** the pre-submit handoff remains reserved until submission becomes visible

#### Scenario: Owner-side fork does not start a second forwarding hop

- **GIVEN** an unanchored request has reached its canonical owner
- **AND** that owner creates an independent fork because the canonical lane is active
- **WHEN** rendezvous hashing the generated fork key would select another instance
- **THEN** the canonical owner creates and durably claims the fork locally
- **AND** the request is not rejected as a forwarding loop

#### Scenario: Blank turn-state is not an anchor

- **GIVEN** a request has a session header and an empty or whitespace-only turn-state header
- **WHEN** the request is forwarded to its owner
- **THEN** the signed forwarding context marks the original request as unanchored
- **AND** the generated downstream turn-state does not collapse it onto the canonical gate

#### Scenario: Forwarding downgrade fails closed

- **GIVEN** an owner-forward request requires unanchored concurrency semantics
- **WHEN** the signed unanchored boolean is changed, removed, or repacked into affinity fields, or either instance only supports the legacy signature
- **THEN** the owner-forward hop fails closed
- **AND** the request is not attached to the shared canonical response-create gate

#### Scenario: Anchored forwarding remains rolling-upgrade compatible

- **GIVEN** an owner-forward request carries explicit previous-response or turn-state continuity
- **WHEN** the origin and owner run different bridge protocol versions
- **THEN** the primary signature remains valid under the legacy contract
- **AND** the anchored request can continue without weakening unanchored fail-closed behavior

#### Scenario: Prompt-cache forwarding remains rolling-upgrade compatible

- **GIVEN** an unanchored first-turn request uses a prompt-cache affinity lane
- **WHEN** that request is forwarded to its canonical owner
- **THEN** the origin does not attach session-header unanchored v2 state
- **AND** an older owner may accept the legacy-compatible forwarding contract

#### Scenario: Legacy session-header canonical lane proves its turn-state anchor

- **GIVEN** a legacy-signed owner forward has no previous-response ID and its durable canonical key is still `session_header`
- **WHEN** its forwarded turn state is a registered durable alias for that exact canonical lane
- **THEN** the current owner accepts it as anchored continuity
- **AND** an unknown turn state or an alias for another canonical lane fails closed with `bridge_forward_upgrade_required`

#### Scenario: Legacy proof precedes compact and bridge fallback branches

- **GIVEN** a legacy-signed owner forward requires turn-state anchor proof
- **WHEN** the request contains a terminal compaction trigger or bypasses the websocket bridge
- **THEN** exact alias proof runs before compact, HTTP fallback, admission, or upstream work

#### Scenario: Current origin proves a turn-state alias before legacy owner forwarding

- **GIVEN** a current origin resolves a nonblank turn state only through a shared `session_header` durable lane
- **WHEN** that request would be forwarded to another owner with the legacy signature contract
- **THEN** the origin proves an exact turn-state alias row for that canonical lane before sending the owner request
- **AND** an unknown alias fails closed with `bridge_forward_upgrade_required`

#### Scenario: Latest-state metadata is not proof of alias registration

- **GIVEN** a durable session records a latest turn state but has no matching turn-state alias row
- **WHEN** that value is presented by a legacy-signed owner forward
- **THEN** the owner rejects it with `bridge_forward_upgrade_required`

#### Scenario: Stale owners cannot register continuity aliases after takeover

- **GIVEN** durable ownership advanced to a new owner epoch
- **WHEN** the stale owner attempts to register a turn-state or previous-response alias with its old epoch
- **THEN** alias registration writes nothing
- **AND** the stale owner removes the rejected value from its local alias index
- **AND** a newer local generation's mapping for the same value remains intact
- **AND** the stale value cannot satisfy legacy anchor proof

#### Scenario: Ambiguous legacy signature fields fail closed

- **GIVEN** a legacy owner-forward signature contains a delimiter in any signed header field
- **WHEN** field boundaries are repacked without changing the legacy joined byte string
- **THEN** a current owner rejects the forwarding context as invalid
- **AND** the repacked affinity kind cannot weaken hard continuity

#### Scenario: V2 client-IP metadata cannot be removed or blanked

- **GIVEN** an unanchored v2 owner-forward request carries signed client-IP metadata
- **WHEN** both client-IP headers are removed, the value is blanked, or the value is changed
- **THEN** the owner rejects the forwarding context as invalid
- **AND** a genuinely no-IP v2 request remains valid

#### Scenario: Durable fork continuation remains owner-bound

- **GIVEN** a forked lane has produced a durable turn-state or previous-response alias
- **WHEN** a later request resolves that alias on another instance
- **THEN** the request follows the hard owner-bound continuity path
- **AND** the original account binding is preserved

#### Scenario: Explicit continuation is not split

- **WHEN** a request carries `previous_response_id` or a turn-state header
- **THEN** the service keeps the request on the hard owner-bound continuity path
- **AND** it does not apply unanchored parallel-session isolation

### Requirement: Unusable account transitions remove persistent affinity bindings

The system SHALL remove persistent affinity bindings when an account becomes
permanently unusable because it requires reauthentication or is deactivated.
This includes durable sticky-session mappings and durable HTTP bridge aliases.
Any durable HTTP bridge rows closed by this transition MUST clear account
ownership, owner leases, and stored continuity anchors so follow-up requests
cannot resolve stale turn-state or previous response aliases through the closed
row.

#### Scenario: Reauthentication requirement clears bridge continuity

- **GIVEN** an account has sticky-session mappings and durable HTTP bridge aliases
- **AND** a bridge row stores the latest turn state and previous response
- **WHEN** the account is marked `reauth_required`
- **THEN** sticky-session mappings for the account are deleted
- **AND** durable HTTP bridge aliases for the account's bridge rows are deleted
- **AND** the bridge rows are closed without account ownership, live owner lease, or stored continuity anchors

#### Scenario: Failed compare-and-swap status transition keeps affinity bindings

- **GIVEN** an account has sticky-session mappings and durable HTTP bridge aliases
- **WHEN** a conditional account status update does not match the expected current row state
- **THEN** the account's sticky-session mappings and durable HTTP bridge aliases remain unchanged

### Requirement: Durable bridge lease writes are fenced
All durable HTTP bridge session lease writes — renewal, release, and continuity-alias registration — MUST be executed as single fenced statements conditioned on the caller's `(owner_instance_id, owner_epoch)` so a fenced-out caller mutates nothing. A fenced-out renewal or release MUST leave the row (owner, lease, state, and `latest_turn_state` / `latest_response_id` continuity anchors) unchanged and MUST report the current owner snapshot to the caller.

#### Scenario: Stale-epoch renewal does not overwrite the new owner
- **GIVEN** replica B took over a durable bridge session, advancing its owner epoch
- **WHEN** replica A renews the session with its stale epoch
- **THEN** the row still shows replica B's ownership, lease, and continuity anchors
- **AND** replica A receives a snapshot identifying replica B as the current owner

#### Scenario: Stale-epoch release does not clear the new owner's lease
- **GIVEN** replica B took over a durable bridge session, advancing its owner epoch
- **WHEN** replica A releases the session with its stale epoch
- **THEN** the row keeps replica B's ownership and ACTIVE state
- **AND** replica A receives a snapshot identifying replica B as the current owner

### Requirement: Fenced-out replicas evict their local bridge session
When a replica discovers through a fenced renewal or fenced alias write that another instance or epoch owns the durable session, it MUST close its local in-memory bridge session — closing the upstream websocket and releasing the account lease — instead of adopting the new epoch and continuing to serve. A replica MUST also reconcile durable ownership for local sessions whose lease is past its TTL on the ring-heartbeat cadence and close any session that has been fenced out, so orphaned upstream connections and account leases are bounded by the lease TTL rather than the idle TTL.

#### Scenario: Fenced-out renewal closes the local session
- **GIVEN** replica A holds a local bridge session and replica B took over the durable row
- **WHEN** replica A's lease renewal is fenced out
- **THEN** replica A detaches and closes the local session, releasing its account lease and upstream websocket
- **AND** the request fails with the retryable bridge-instance-mismatch error instead of riding the fenced-out session

#### Scenario: Heartbeat reconciliation closes fenced-out idle sessions
- **GIVEN** replica A holds an idle local bridge session whose durable lease expired
- **AND** replica B has since claimed the durable row
- **WHEN** replica A's heartbeat reconciliation sweep runs
- **THEN** replica A closes the fenced-out local session
- **AND** local sessions still owned by replica A are left untouched

#### Scenario: Reconciliation lookups survive large candidate sets
- **GIVEN** more local sessions are past the lease TTL than fit in one database `IN (...)` parameter list
- **WHEN** the reconciliation sweep batch-loads the durable rows
- **THEN** the lookup is chunked so every candidate resolves and fenced-out sessions are still evicted

### Requirement: Abandoned durable bridge rows are purged
The background cleanup loop MUST delete ACTIVE and DRAINING `http_bridge_sessions` rows whose lease is expired and whose `last_seen_at` predates the retention cutoff, deleting their aliases in the same pass, so crashed-owner and abandoned-drain rows do not accumulate. Rows with an unexpired lease or recent activity MUST NOT be deleted so crash takeover and drain recovery keep their continuity anchors. The retention cutoff MUST be at least the longest effective bridge session reuse window — the maximum of the prompt-cache affinity max age, the prompt-cache bridge idle TTL, the codex bridge idle TTL, and the base bridge idle TTL — so an idle-but-still-reusable local session never loses its ACTIVE durable row and aliases while it can still be reused.

#### Scenario: Expired abandoned rows are purged with their aliases
- **WHEN** the cleanup loop runs
- **AND** an ACTIVE row's lease expired and its `last_seen_at` is older than the retention cutoff
- **THEN** the row and its aliases are deleted

#### Scenario: Recent or live-lease rows survive the purge
- **WHEN** the cleanup loop runs
- **AND** a row holds an unexpired lease or has `last_seen_at` within the retention cutoff
- **THEN** the row and its aliases are preserved

#### Scenario: In-reuse-window prompt-cache sessions keep their durable row
- **GIVEN** the prompt-cache bridge idle TTL exceeds the prompt-cache affinity max age
- **WHEN** the cleanup loop runs against an ACTIVE row whose lease expired but whose `last_seen_at` is within the prompt-cache bridge idle TTL
- **THEN** the row and its aliases are preserved so a local reuse keeps its durable ownership and continuity anchors
