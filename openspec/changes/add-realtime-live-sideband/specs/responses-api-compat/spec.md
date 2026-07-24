## ADDED Requirements

### Requirement: Codex realtime calls retain exact account ownership for Frameless sideband attachment

When a valid proxy API key authenticates `POST /backend-api/codex/realtime/calls`, even while ordinary proxy API-key auth is disabled, and the request completes successfully with a documented upstream `Location` shape containing a bounded ASCII `rtc_...` or canonical UUID call id, the proxy SHALL bind that call to the final ChatGPT account that completed the HTTP request. The binding MUST be scoped to that proxy API key, MUST be usable across replicas, MUST be immutable once inserted, and MUST persist only a bounded digest in a reserved non-user-forgeable namespace rather than the raw call id, API key, OAuth token, SDP, attestation value, or frame payload. The binding MUST expire after a fixed bounded interval and stale reserved rows MUST be cleaned in throttled bounded batches without requiring a new operator setting.

#### Scenario: initial selected account creates the call

- **GIVEN** a valid proxy API key and an eligible ChatGPT account
- **WHEN** the account completes `POST /backend-api/codex/realtime/calls` and upstream returns `Location: .../rtc_example`
- **THEN** the proxy returns the upstream status, body, and allowlisted response headers unchanged
- **AND** it persists an API-key-scoped digest mapping for `rtc_example` to that account
- **AND** it does not persist the raw call id, SDP, bearer token, or attestation value

#### Scenario: realtime call requires an API key in trusted auth-disabled mode

- **GIVEN** ordinary proxy API-key authentication is disabled
- **WHEN** a client calls `POST /backend-api/codex/realtime/calls` without a valid proxy API key
- **THEN** the proxy rejects the request before selecting or contacting an upstream account
- **AND** it does not create an anonymous owner namespace

#### Scenario: pre-visible failover creates the call on another account

- **GIVEN** the first selected account fails before the call-creation response is visible
- **AND** existing Codex control failover selects another eligible account
- **WHEN** the replacement account successfully creates the call
- **THEN** the call binding names the replacement account
- **AND** it does not name the initial failed account

#### Scenario: successful response lacks a valid call id

- **WHEN** upstream returns a successful realtime-call response without a bounded supported call id in a documented `Location` path
- **THEN** the proxy preserves the existing call-creation response contract
- **AND** it does not create an owner binding

#### Scenario: a digest is already bound to another owner

- **GIVEN** the API-key-scoped call digest is already bound to `account_a`
- **WHEN** a successful control response attempts to bind that same digest to `account_b`
- **THEN** the proxy fails the downstream response closed as an integrity failure
- **AND** the persisted owner remains `account_a`

#### Scenario: binding persistence fails

- **WHEN** upstream creates a call but the proxy cannot durably bind its owner before exposing the response
- **THEN** the proxy fails the downstream request closed
- **AND** it does not retry the already-created call through another account
- **AND** it does not penalize the account for the local persistence failure

#### Scenario: expired abandoned bindings are bounded

- **GIVEN** realtime-call owner rows older than the fixed binding lifetime
- **WHEN** an expired binding is resolved
- **THEN** that exact stale row is deleted while the lookup fails closed
- **WHEN** successful binding triggers opportunistic cleanup after its throttle interval
- **THEN** at most one configured-size batch of additional stale rows in the reserved realtime-call namespace is deleted
- **AND** unrelated sticky-session rows remain unchanged

### Requirement: Frameless live WebSockets use the bound call account without refresh or failover

The proxy SHALL expose `WS /v1/live/{call_id}` forwarding only to a valid proxy API key, even when ordinary proxy API-key authentication is disabled, and only for that key's live binding. It MUST re-evaluate current API-key validity and assigned-account scope, resolve the exact bound account as hard ownership, acquire that account's stream capacity under reattach policy, fresh-load that leased owner's currently persisted credential and identity snapshot at the credential-use boundary, and connect to `wss://api.openai.com/v1/live/{call_id}`. The attach MUST NOT refresh the account token, select another account, replay a definitive handshake denial through another route endpoint, replay the handshake through another account, or mutate global account health on capability-specific failure.

#### Scenario: live attachment requires an API key in trusted auth-disabled mode

- **GIVEN** ordinary proxy API-key authentication is disabled
- **WHEN** a client opens `WS /v1/live/rtc_example` without a valid proxy API key
- **THEN** the proxy rejects the handshake before looking up call ownership
- **AND** possession of the call id alone grants no access

#### Scenario: bound client attaches to the live sideband

- **GIVEN** `rtc_example` is bound to `account_a` under `api_key_a`
- **WHEN** `api_key_a` opens `WS /v1/live/rtc_example`
- **THEN** the proxy selects `account_a` as required continuity ownership
- **AND** it reserves and later releases `account_a` stream capacity
- **AND** it opens upstream `/v1/live/rtc_example` with `account_a` credentials

#### Scenario: call creation refreshed the owner while routing inputs remained cached

- **GIVEN** call creation first used rejected credential snapshot `token_a`
- **AND** its forced refresh committed `token_b` plus updated account and installation identity before successfully creating and binding the call
- **AND** the routing-selection cache still contains the pre-refresh `token_a` snapshot
- **WHEN** the client immediately attaches the live sideband
- **THEN** the proxy fresh-loads the exact leased owner from persistent storage before credential decryption and route resolution
- **AND** it opens upstream with `token_b` and the updated persisted identities
- **AND** it does not invoke token refresh or account failover during attachment

#### Scenario: another API key knows the call id

- **GIVEN** `rtc_example` was created under `api_key_a`
- **WHEN** valid `api_key_b` attempts `WS /v1/live/rtc_example`
- **THEN** the proxy behaves as though no live binding exists for that caller
- **AND** it does not reveal or use the owner selected for `api_key_a`

#### Scenario: assigned-account policy changed after call creation

- **GIVEN** the bound account is no longer permitted by the API key's current account-assignment scope
- **WHEN** the sideband is opened
- **THEN** the proxy denies the attachment
- **AND** it does not bypass current scope using the stale binding
- **AND** it does not select another account

#### Scenario: bound owner is unavailable or over capacity

- **WHEN** the exact owner cannot receive the sideband under current account and stream-capacity policy
- **THEN** the proxy fails closed with a credential-safe error
- **AND** it neither refreshes that account nor attaches the call through another account

#### Scenario: sideband handshake is denied upstream

- **WHEN** upstream rejects the exact-account WebSocket handshake with `401`, `403`, or another status
- **THEN** the downstream client receives the normalized original HTTP handshake status without route endpoint details
- **AND** the proxy does not refresh, retry that definitive denial through a route fallback, select another account, or mark the account globally unhealthy solely because of that denial

### Requirement: Frameless live WebSocket forwarding preserves control frames and required headers

The live connector MUST replace downstream proxy authorization, ChatGPT account identity, and client-supplied Codex installation identity with the bound account identity; preserve downstream Frameless parser/architecture query parameters and filtered Codex protocol/session/originator/attestation headers; omit Responses-only WebSocket beta headers; and apply existing upstream egress-routing policy. After connection it MUST relay text and binary frames without parsing or mutation, preserve bounded valid close codes/reasons in both directions, enforce the existing WebSocket message-size boundary, use transport-native ping/pong liveness, and release all connection, route, and lease resources on disconnect or cancellation. The change MUST NOT emit realtime SDP through opt-in payload tracing or add attestation, token, transcript, audio, frame-body, raw live-call path, or live-query logging inside Codex-LB.

#### Scenario: Codex Frameless handshake headers are preserved safely

- **WHEN** a bound client supplies Frameless `intent`/`architecture` query parameters, `OpenAI-Alpha: quicksilver=v2`, session headers, originator, and `x-oai-attestation`
- **THEN** the upstream handshake receives those query parameters and filtered header values
- **AND** it receives the bound account bearer, ChatGPT account id, and canonical persisted installation id when available
- **AND** it does not receive the downstream Codex-LB bearer, client-supplied installation id, or a Responses-WebSocket beta header

#### Scenario: direct and routed egress obey existing policy

- **WHEN** the bound account has a resolved upstream proxy route
- **THEN** the live WebSocket opens through that route and records credential-safe route metadata
- **WHEN** no route is required
- **THEN** direct egress occurs only through the existing explicit direct-egress opt-in

#### Scenario: text and binary frames are transparent

- **WHEN** either peer emits a text or binary frame after the handshake
- **THEN** the opposite peer receives byte-equivalent content
- **AND** the proxy does not interpret, synthesize, or authorize Frameless event payloads

#### Scenario: diagnostic channels remain content-free

- **GIVEN** upstream payload tracing and Responses frame archiving are enabled
- **WHEN** a realtime SDP offer is sent and a live WebSocket is opened
- **THEN** the SDP and live frame bodies are absent from traces and archives
- **AND** Codex-LB handshake logs use a redacted live path and omit the live query string

#### Scenario: either peer disconnects

- **WHEN** the downstream or upstream peer closes or the handler is cancelled
- **THEN** the opposite connection is closed deterministically with the peer's valid bounded close code and reason when supplied
- **AND** the paired relay task is cancelled and awaited
- **AND** the account stream lease and upstream client resources are released exactly once

#### Scenario: public Realtime API remains distinct

- **WHEN** a client inspects the model catalog or documented OpenAI-compatible routes
- **THEN** this private Codex transport does not advertise GPT-Live as a public model
- **AND** it does not add or reinterpret `/v1/realtime/calls`
