# upstream-proxy-routing Specification

## Purpose
TBD - created by archiving change add-codex-proxy-pool-egress. Update Purpose after archive.

## Requirements

### Requirement: Account-bound upstream traffic must use the bound proxy pool
When an account has an explicit upstream proxy pool binding, every ChatGPT/OpenAI/Codex upstream operation using that account's credentials MUST resolve a route from the bound pool before opening a network connection.

#### Scenario: Bound pool unavailable fails closed
- **GIVEN** an account has an explicit upstream proxy pool binding
- **AND** the bound pool has no active usable endpoint
- **WHEN** an account-scoped ChatGPT upstream operation is attempted
- **THEN** the operation MUST fail before opening an upstream network connection
- **AND** it MUST NOT use the default pool, environment proxy, or direct egress.

#### Scenario: Warmup and compact operations obey account-bound routing
- **GIVEN** an account has an explicit upstream proxy pool binding
- **WHEN** the system performs warmup or compact Responses operations with that account's credentials
- **THEN** the operation MUST resolve and use a route from the bound pool before opening the upstream connection
- **AND** it MUST fail closed instead of falling back to direct egress when no bound route is available.

#### Scenario: Auth import does not perform direct usage refresh when proxy routing is required
- **GIVEN** upstream proxy routing is enabled
- **AND** an imported account has no usable account-bound or default proxy route
- **WHEN** an operator imports that account from `auth.json`
- **THEN** the import MUST save the account as paused before any usage-refresh network request is opened
- **AND** it MUST NOT perform the import-time usage refresh through direct egress.

#### Scenario: Proxy binding releases import-paused account
- **GIVEN** an account was paused because proxy routing was required during `auth.json` import
- **WHEN** an operator saves an active upstream proxy binding for that account
- **THEN** the account SHALL be reactivated so it can enter the routed account pool.

### Requirement: Codex upstream Codex client must require a resolved route and built-in TLS fingerprint
Affected Codex upstream HTTP and websocket calls MUST use the Codex upstream client with an explicit resolved route and the built-in Codex CLI TLS fingerprint.

#### Scenario: Runtime fingerprint override rejected
- **WHEN** a caller attempts to pass runtime fingerprint kwargs such as `impersonate`, `ja3`, `akamai`, or `extra_fp`
- **THEN** the client MUST reject the call before opening a network connection.

### Requirement: Route metadata must be persisted for migrated upstream calls
Request logs for migrated upstream calls MUST record route mode, proxy pool id, proxy endpoint id, same-pool fallback use, and fail-closed reason where applicable.

#### Scenario: Fail-closed reason recorded
- **GIVEN** route resolution fails closed before network open
- **WHEN** the request log is written
- **THEN** the log MUST include the fail-closed reason without proxy credentials.

### Requirement: Codex installation metadata must be account-owned

Codex `response.create` requests sent through account-scoped HTTP/SSE, bridge,
or WebSocket transports MUST use the selected local account's stored
`x-codex-installation-id` value in `client_metadata`. For the observed Codex CLI
0.150.1 Responses wire profile, the same value MUST NOT be synthesized as a
standalone upstream HTTP request or WebSocket handshake header. Header location
is part of the wire profile and MUST be selected per transport rather than
inferred from the existence of an account installation id.

#### Scenario: Client-supplied installation id is replaced

- **GIVEN** a client sends `client_metadata.x-codex-installation-id`
- **AND** codex-lb selects account `A`
- **WHEN** codex-lb sends the upstream `response.create` request
- **THEN** the upstream `client_metadata.x-codex-installation-id` MUST equal account `A`'s stored installation id
- **AND** it MUST NOT equal the client-supplied value.

#### Scenario: Profiled Responses egress omits standalone installation header

- **GIVEN** codex-lb selects an account with a stored installation id
- **WHEN** it opens a Codex CLI 0.150.1-profiled Responses HTTP/SSE request or WebSocket connection
- **THEN** the selected account installation id MUST be present in each `response.create.client_metadata`
- **AND** `x-codex-installation-id` MUST be absent from the standalone upstream request or handshake headers.

### Requirement: Upstream proxy pool membership must reject duplicates
Dashboard upstream proxy pool member mutations MUST reject attempts to add an endpoint that is already a member of the target pool with a validation error instead of surfacing a database integrity failure.

#### Scenario: Duplicate pool member rejected
- **GIVEN** a proxy pool already contains endpoint `E`
- **WHEN** an admin adds endpoint `E` to the same pool again
- **THEN** the API MUST return a dashboard validation error
- **AND** it MUST NOT return an unhandled server error.

### Requirement: Routed WebSocket connection failures preserve transport errors

The Codex upstream client MUST invoke an asynchronous WebSocket context
manager's exit method only after that context manager has been entered
successfully. If context entry fails, the client MUST preserve the original
connection or handshake failure for credential-safe transport classification
and MUST apply the configured same-pool endpoint fallback policy to that
failure.

#### Scenario: Connection failure before context entry is not masked

- **GIVEN** an awaitable WebSocket context manager whose connection attempt fails before entry completes
- **WHEN** the Codex upstream client opens the routed WebSocket
- **THEN** the client does not invoke the unentered context manager's exit method
- **AND** it returns a credential-safe transport error classified from the original connection failure
- **AND** it does not replace that failure with a cleanup exception

#### Scenario: Unmasked failure can use the next route endpoint

- **GIVEN** a routed WebSocket connection whose first endpoint fails before context entry
- **AND** same-pool network-error fallback is enabled
- **WHEN** another endpoint remains in the resolved route
- **THEN** the client attempts the next endpoint
- **AND** the first endpoint's unentered context manager is not exited

#### Scenario: Successfully entered context retains caller-owned cleanup

- **GIVEN** a routed WebSocket context manager enters successfully
- **WHEN** the client returns the opened WebSocket and its context to the caller
- **THEN** the caller can exit the returned context using the existing ownership contract

### Requirement: Cached route resolution preserves fail-closed semantics

Any cache in front of upstream-route resolution MUST store the resolver's outcome verbatim — a resolved route, a permitted direct-egress `None`, or a fail-closed error with its reason. A cache hit MUST reproduce that outcome exactly: it MUST NOT convert a fail-closed outcome or a routed outcome into direct egress, and it MUST NOT substitute a different pool or endpoint than the resolver chose. Cache staleness MUST be bounded by invalidation on admin mutations (same-replica: before the mutating response returns; peers: within one cache-invalidation poll interval) with a TTL backstop for out-of-band edits.

#### Scenario: Cached fail-closed outcome keeps failing closed

- **GIVEN** an account-bound pool with no active usable endpoint whose fail-closed resolution outcome is cached
- **WHEN** further upstream operations are attempted for that account
- **THEN** each operation MUST fail before opening an upstream network connection with the same fail-closed reason
- **AND** it MUST NOT use the default pool, environment proxy, or direct egress

#### Scenario: New binding takes effect without a direct-egress window on the mutating replica

- **GIVEN** an account whose cached resolution outcome is direct-egress `None`
- **WHEN** an operator saves an active proxy binding for that account
- **THEN** the mutating replica's cached outcome MUST be invalidated before the binding response returns, so subsequent requests on that replica resolve the bound pool

### Requirement: Confirmed account-proxy connection failures fail over safely

When an account-routed transport reports that it could not connect to the selected proxy endpoint and proves that the upstream request was not dispatched, the service MUST classify the failure with sanitized structured pre-dispatch provenance. For a route with another usable endpoint in the same proxy pool, the client MUST try that endpoint before moving accounts, including for a non-idempotent request. If the pool cannot connect, movable Responses requests MUST exclude the failed account and retry another eligible account within the existing request budget and attempt limits.

This behavior MUST cover raw HTTP/SSE, native Responses WebSocket, and the HTTP responses bridge. Before recording transient account backoff, the service MUST release response-create and stream leases held for the failed account. A request-scoped API-key reservation MUST remain singular across an internal pre-dispatch failover, MUST settle or release at the terminal request outcome before the account-health write, and MUST NOT be reacquired solely for the internal failover. If neither settlement nor fallback release can be confirmed, the service MUST leave the health write unapplied. HTTP-bridge startup cleanup MUST release only an unowned current request lifecycle, and each reservation lifecycle MUST drain only its own health writes after confirmed settlement or release. The confirmed failure MUST place the account at the existing bounded transient error-backoff floor, but MUST NOT pause, deactivate, rate-limit, or quota-penalize it.

The service MUST NOT replay a request when dispatch is unknown or when the request depends on hard previous-response, turn-state, uploaded-file, single-account, or other required account ownership. If no eligible replacement account exists, the service MUST preserve the original sanitized upstream-unavailable failure instead of replacing it with a generated `no_accounts` error.

#### Scenario: POST uses a healthy endpoint from the same proxy pool

- **GIVEN** a non-idempotent Responses POST is routed through a proxy pool with two endpoints
- **AND** connecting to the first endpoint fails before request dispatch
- **WHEN** the second endpoint is reachable
- **THEN** the service sends the request through the second endpoint
- **AND** it does not move the request to another account

#### Scenario: movable request retries another account

- **GIVEN** two eligible accounts and the first account's complete proxy route refuses connections before dispatch
- **WHEN** a fresh Responses request has no hard account ownership
- **THEN** the service releases the first account's response-create and stream leases
- **AND** it settles or releases any request-scoped API-key reservation before the account-health write
- **AND** it records bounded transient backoff for the first account
- **AND** it excludes the first account and completes through the second account
- **AND** no failure event from the first attempt is forwarded downstream

#### Scenario: hard account ownership fails closed

- **GIVEN** a Responses request depends on a previous-response owner or an account-scoped uploaded file
- **AND** the required account's proxy refuses the connection before dispatch
- **WHEN** another account is otherwise eligible
- **THEN** the service does not send the request to the other account
- **AND** it returns the sanitized upstream-unavailable failure for the required account

#### Scenario: ambiguous transport failure is not replayed

- **WHEN** a POST transport failure cannot prove that request dispatch was impossible
- **THEN** the service does not use that failure as authorization to retry another proxy endpoint or account

#### Scenario: empty replacement pool preserves the original failure

- **GIVEN** a movable request has a confirmed pre-dispatch proxy connection failure
- **AND** no other eligible account can be selected
- **THEN** the client receives the original sanitized upstream-unavailable failure
- **AND** the failure is not replaced with `no_accounts`

### Requirement: New accounts receive a balanced proxy-pool binding

When an `auth.json` import or untargeted OAuth completion creates a new local
account row, the system MUST create an active binding to an active proxy pool
that has at least one active member backed by an active endpoint, when such a
pool exists. The selected pool MUST have the fewest active account bindings at
selection time, with a stable deterministic tie-break. The account and binding
MUST become durable in the same transaction, and upstream-route caches MUST be
invalidated after commit.

Automatic assignment MUST be independent of the global upstream-proxy routing
toggle because an explicit account binding is itself the routing contract. The
system MUST NOT replace or reactivate an existing binding when an existing
account row is re-imported or reauthenticated. If no structurally usable pool
exists, account creation MUST retain the existing unbound import or OAuth
behavior.

#### Scenario: Imported account is bound before usage refresh

- **GIVEN** at least one active proxy pool has an active member and endpoint
- **WHEN** a valid `auth.json` import creates a new local account row
- **THEN** the account and an active binding to a least-loaded pool are committed atomically
- **AND** any import-time usage refresh resolves the new account-bound route

#### Scenario: New OAuth account receives an initial binding

- **GIVEN** at least one active proxy pool has an active member and endpoint
- **WHEN** an untargeted OAuth completion creates a new local account row
- **THEN** the account receives an active binding to a least-loaded pool
- **AND** the upstream-route cache is invalidated after the binding commits

#### Scenario: Sequential account additions remain balanced

- **GIVEN** multiple structurally usable pools
- **WHEN** new accounts are created one after another
- **THEN** each assignment selects a pool with the fewest active account bindings at that selection
- **AND** equal-load ties are resolved in a stable deterministic order

#### Scenario: Existing account binding is preserved

- **GIVEN** an existing account has an active or inactive proxy-pool binding
- **WHEN** that account is re-imported or targeted for reauthentication
- **THEN** automatic assignment does not replace the binding
- **AND** it does not change the binding's active state

#### Scenario: No usable pool preserves existing account creation behavior

- **GIVEN** no active proxy pool has both an active member and active endpoint
- **WHEN** an import or OAuth completion creates a new local account row
- **THEN** no automatic binding is created
- **AND** the existing unbound success or fail-closed behavior applies
