# sticky-session-operations Specification

## Purpose

Define sticky-session operation contracts so durable sessions, dashboard affinity, and prompt-cache affinity stay distinct.
## Requirements
### Requirement: Sticky sessions are explicitly typed
The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently.

#### Scenario: Sticky reallocation uses split primary and secondary pressure thresholds
- **WHEN** a request resolves an existing sticky-session mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either the configured primary sticky reallocation threshold or the configured secondary sticky reallocation threshold
- **AND** another eligible account remains at or below both configured sticky reallocation thresholds
- **THEN** selection rebinds the sticky-session mapping to the healthier account before sending the request upstream

#### Scenario: Sticky reallocation preserves a pinned account when every candidate is split-threshold pressured
- **WHEN** a request resolves an existing sticky-session mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either configured sticky reallocation threshold
- **AND** every other eligible account is also strictly above at least one configured sticky reallocation threshold
- **THEN** selection retains the existing pinned account to avoid sticky-pin thrashing

#### Scenario: Fresh selection does not apply sticky secondary pressure threshold
- **WHEN** a request has no sticky-session mapping
- **AND** one eligible account is above the configured secondary sticky reallocation threshold but below the normal primary budget threshold
- **THEN** the account remains eligible for ordinary non-sticky routing according to the selected routing strategy

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

Requests that depend on `previous_response_id`, hard turn-state, account-scoped `input_file.file_id` pins, or another required owner continuity source MUST NOT silently reroute to an account that cannot preserve continuity. A `previous_response_id` is a stored-object continuation reference and remains owner-bound even when the same request also carries `prompt_cache_key` or another soft locality key. If the owner account/session is unavailable or saturated, the service MUST fail closed with an explicit retryable continuity/local overload reason instead of flooding the owner queue indefinitely.

#### Scenario: Previous-response owner queue is saturated

- **WHEN** a `/v1/responses` follow-up requires a previous-response owner
- **AND** the owner session queue or account cap is saturated
- **THEN** the service fails closed with `hard_affinity_saturated` or `previous_response_owner_unavailable`
- **AND** it does not route to an unrelated account that lacks continuity state

#### Scenario: File-pinned request owner is capped

- **WHEN** a `/v1/responses` request references an `input_file.file_id` pinned to an owner account
- **AND** the owner account is at its account stream or response-create cap
- **THEN** the service returns a local account-cap overload for the owner
- **AND** it does not route the file reference to another account

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

