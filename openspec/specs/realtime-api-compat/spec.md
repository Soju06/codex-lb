# realtime-api-compat Specification

## Purpose

Define private Codex Live Voice call-owner continuity, authenticated sideband routing, transport privacy, and dashboard/operator contracts without implementing the public OpenAI Realtime API.

## Requirements

### Requirement: Call creation binds the final account under an authenticated caller scope

`POST /backend-api/codex/realtime/calls` SHALL require either a registered Proxy API Key or a locally admitted keyless OAuth caller. A `sk-clb-` bearer SHALL use strict Key validation. Every other bearer SHALL pass the ordinary zero-key proxy origin contract before policy lookup: global API-key authentication is disabled, and the connection is loopback or its raw socket peer belongs to the existing explicit unauthenticated CIDR allowlist. Keyless OAuth SHALL require a normalized `chatgpt-account-id` and an active global policy with at least one currently active allowed Account. After a successful upstream response with a root-relative or absolute `Location` whose parsed path is exactly `/v1/realtime/calls/{call_id}`, where `{call_id}` is a bounded ASCII `rtc_...` or canonical UUID, the proxy MUST bind the call immutably to the final ChatGPT account that completed the request. Relative paths without the leading `/`, unrelated path prefixes, abbreviated `/live/...` or `/realtime/calls/...` paths, and paths with extra segments are unsupported. The binding MUST be scoped to the caller, MUST persist across replicas as only a bounded digest in a reserved non-user-forgeable namespace, MUST expire after a fixed interval, and MUST NOT persist the raw call id, API key, OAuth token, account header, SDP, attestation value, or frame body. Key callers retain `SHA256(api_key.id + NUL + call_id)`. Keyless callers use `oauth-local:HMAC-SHA256(K, bearer + NUL + normalized-account-id)` as scope material, where `K` is purpose-separated from the existing persistent encryption key. Private call-creation diagnostics MUST redact internal account identifiers and suppress exception details.

#### Scenario: initial or replacement account creates the call

- **GIVEN** a registered proxy key and one or more eligible accounts
- **WHEN** the initial account, a pre-visible failover account, or a refreshed account successfully returns `Location: /v1/realtime/calls/rtc_example`
- **THEN** the proxy binds `rtc_example` to the final successful account under that key
- **AND** it returns the upstream status, body, `Location`, and allowlisted response headers unchanged

#### Scenario: call Location carries private query context

- **WHEN** successful call creation returns an exact supported path followed by `?` query text and an optional fragment
- **THEN** the proxy binds only the bounded call id parsed from the path before the first `?`
- **AND** it neither persists nor logs the discarded query or fragment text

#### Scenario: private call creation has no authenticated caller

- **GIVEN** ordinary proxy authentication is disabled
- **WHEN** a caller omits authorization, supplies a malformed bearer, or supplies an unregistered `sk-clb-` Key to realtime call creation
- **THEN** the proxy rejects the request before selecting or contacting an upstream account
- **AND** it does not create an anonymous ownership namespace

#### Scenario: local keyless OAuth caller creates a call

- **GIVEN** global API-key authentication is disabled, the request passes the existing zero-key origin guard, and the global policy has eligible serving Accounts
- **WHEN** call creation succeeds
- **THEN** the final serving Account binds under the credential-pair HMAC scope
- **AND** request logging uses nullable API-key attribution

#### Scenario: remote OAuth caller is outside the zero-key boundary

- **WHEN** a non-Key bearer arrives from a connection that fails the existing zero-key origin guard, or while global API-key authentication is enabled
- **THEN** the route returns `401 invalid_api_key` before policy lookup or account selection
- **AND** projected loopback, Host, and forwarded-header hints do not override the preserved raw-socket and trusted-proxy checks

#### Scenario: registered Key remains compatible with Codex conversations

- **GIVEN** a Codex provider uses `requires_openai_auth = true` and a registered Key through `env_key`
- **WHEN** the client sends ordinary conversation traffic or creates and attaches a Live call
- **THEN** existing Key assignments, limits, attribution, and caller-scoped ownership remain unchanged

#### Scenario: global OAuth policy is inactive

- **WHEN** valid OAuth credentials reach Live while the global policy is inactive or empty after active-account filtering
- **THEN** the route returns `403 oauth_live_not_enabled` before account selection

#### Scenario: successful response cannot be bound

- **WHEN** upstream returns success without a root-relative or absolute `Location` whose parsed path is exactly `/v1/realtime/calls/{bounded_call_id}`, or durable owner binding fails
- **THEN** the proxy returns one `503` with code `realtime_call_binding_failed`
- **AND** it does not expose or replay the already-created upstream call through another account
- **AND** the single private call-creation request row is persisted with error status

#### Scenario: ownership and cleanup remain bounded

- **WHEN** insertion encounters the same digest and owner
- **THEN** it is idempotent
- **WHEN** insertion encounters a different owner for that digest
- **THEN** it preserves the original owner and fails closed
- **WHEN** a binding expires or opportunistic cleanup runs
- **THEN** expiry removal is conditional on the owner and timestamp observed as expired, or cleanup removes one bounded reserved-prefix batch
- **AND** unrelated sticky-session rows remain unchanged

#### Scenario: private freshness diagnostics remain account-safe

- **GIVEN** private call creation reaches account freshness or legacy account-id metadata backfill
- **WHEN** shared refresh cleanup or caller-local metadata persistence emits a warning
- **THEN** the warning contains no internal account identifier, exception text, or traceback
- **AND** shared refresh safety does not depend on whether an ordinary or private caller created the singleflight task

### Requirement: Every sideband route uses the exact bound owner without refresh or failover

The proxy SHALL expose `WS /backend-api/codex/{call_id}`, `WS /v1/live/{call_id}`, and `WS /v1/realtime?call_id={call_id}` to registered Proxy API Keys and locally admitted keyless OAuth callers. All three routes SHALL use the same caller resolver and zero-key origin guard as call creation. All adapters MUST use one bounded call-id normalizer, current caller policy check, exact-owner selection, fresh owner load, reattach stream lease, relay, and connector service. Keyless sideband SHALL recompute the credential-pair HMAC and confirm that the immutable owner remains in the current global allowed set. Current-app and v3 ingress MUST reject any downstream `call_id` query parameter before entering the live service or connector and MUST NOT reconcile it with the path id. Current-app and v3 ingress MUST connect upstream to `/v1/live/{call_id}`. Legacy ingress MUST consume exactly one downstream `call_id` and append its normalized value once after remaining ordered query pairs to `/v1/realtime`.

#### Scenario: returned Location joins through every supported ingress

- **GIVEN** call creation returned a bound `rtc_...` or canonical UUID id
- **WHEN** the caller opens the current-app path, v3 path, or legacy query route
- **THEN** all three resolve and lease the same immutable owner
- **AND** current-app and v3 use `/v1/live/{call_id}` upstream
- **AND** legacy uses `/v1/realtime?<remaining ordered query>&call_id={call_id}` with one final `call_id`

#### Scenario: unrelated Codex WebSocket path remains outside Live

- **WHEN** a caller opens a one-segment `/backend-api/codex/{value}` path whose value is neither a bounded `rtc_...` id nor a canonical UUID
- **THEN** the dedicated Live route does not match
- **AND** the request remains available to ordinary Codex WebSocket routing

#### Scenario: path ingress also supplies a query call id

- **WHEN** a caller opens the current-app or v3 path with any `call_id` query parameter, whether it matches or conflicts with the path id
- **THEN** the proxy rejects the handshake with `400 invalid_realtime_call_id` before owner resolution or upstream connection
- **AND** it does not silently choose, duplicate, or reorder either id

#### Scenario: caller or owner policy changed

- **WHEN** another caller knows the call id, the owner leaves the caller's current allowed scope, or the owner is missing, paused, deleted, capped, or unavailable
- **THEN** attachment fails closed without revealing or substituting the owner
- **AND** it neither refreshes credentials nor selects another account

#### Scenario: keyless credential changes during a call

- **WHEN** sideband supplies a different bearer or normalized `chatgpt-account-id` from call creation
- **THEN** it resolves a different ownership digest and returns the credential-safe not-found response
- **AND** the client creates a new call after OAuth credential rotation

#### Scenario: refreshed call owner attaches with current identity

- **GIVEN** call creation refreshed and persisted the final owner's token or identity while routing inputs remained cached
- **WHEN** the sideband attaches
- **THEN** the service fresh-loads that same leased owner from persistence
- **AND** the connector uses the current persisted bearer, account, installation, and route identity
- **AND** the stream lease is released exactly once

### Requirement: Realtime forwarding preserves protocol context, privacy, and deterministic ownership

The live connector MUST replace downstream proxy authorization, account identity, and client-supplied installation identity with the bound owner identity. It MUST preserve remaining ordered query pairs and supplied version-specific alpha value or absence, FedRAMP, residency, session/context, originator, and attestation headers; strip Responses-only beta values; synthesize neither `OpenAI-Beta` nor `Sec-WebSocket-Protocol`; and apply existing egress policy. It MUST pass the exact ordered downstream WebSocket subprotocol offers through the transport negotiation API, MUST accept downstream only with an upstream-selected value that the downstream offered, and MUST preserve no selection when upstream selects none. It MUST relay text and binary messages without interpretation, preserve only bounded valid close data, enforce the existing message-size boundary, and close/cancel/await each owned peer or task at most once. Both the initial upstream close and its post-cancel drain MUST be bounded; cancellation-resistant cleanup MUST NOT delay handler completion or stream-lease release, and any eventual late task result MUST be consumed without exposing its details.

#### Scenario: protocol-faithful handshake

- **WHEN** a live caller supplies supported query and context headers
- **THEN** upstream receives those values in their required order with bound-owner credentials
- **AND** it does not receive the downstream proxy bearer, client installation identity, duplicate call id, Responses beta, or synthesized subprotocol
- **AND** any ordered subprotocol offers are negotiated upstream without a raw duplicated header
- **AND** downstream receives only the upstream-selected offered value, or no value when upstream selects none

#### Scenario: definitive denial and proxy errors remain isolated

- **WHEN** routed upstream returns a definitive handshake status
- **THEN** the proxy preserves the normalized status without route or credential details and does not replay the denial
- **WHEN** the live connector raises `InvalidProxy`, `InvalidHandshake`, or `OSError`
- **THEN** the sideband receives a fixed capability-specific, credential-safe message
- **AND** ordinary Responses WebSocket exception behavior remains unchanged

#### Scenario: either peer disconnects or connection is cancelled

- **WHEN** a peer closes, a paired relay finishes, or the handler/connection attempt is cancelled
- **THEN** the opposite peer receives only a valid bounded close code/reason when available
- **AND** paired work is cancelled and awaited
- **AND** each peer, connector, and stream lease is released at most once
- **AND** a close task that ignores cancellation is awaited only through a fixed post-cancel drain cap before lease release continues

#### Scenario: diagnostics remain content-free

- **GIVEN** payload tracing and Responses frame archiving are enabled
- **WHEN** call creation carries SDP or the sideband carries realtime frames
- **THEN** SDP and frame bodies are absent from traces and archives
- **AND** sideband rows use `request_kind=realtime_live`, `transport=websocket`, and a redacted path
- **AND** persisted private call-creation and sideband rows omit account identity, model content, upstream error text, failure metadata, live query text, and credentials

### Requirement: Internal ownership and request logs honor dashboard contracts

Reserved realtime ownership is internal continuity state. Ordinary sticky-session operator APIs MUST hide it and MUST NOT delete it through single, bulk, filtered, or delete-all operations. The dashboard `RequestLogsResponseSchema` MUST accept a persisted full request row whose `requestKind` is `realtime_live` and transport is `websocket`; it MUST retain a closed enum rather than weakening request kinds to arbitrary strings.

OAuth Live rows SHALL persist `api_key_id = NULL`. Key rows SHALL retain their existing API-key attribution. Both forms SHALL preserve the same credential-safe payload exclusions.

#### Scenario: reserved owner is not an operator session

- **GIVEN** reserved realtime ownership and ordinary sticky-session rows exist
- **WHEN** an operator lists sessions or performs single, bulk, filtered, or delete-all cleanup
- **THEN** reserved rows are absent from list results and remain unchanged
- **AND** ordinary matching rows retain their existing behavior

#### Scenario: Recent Requests consumes a live sideband row

- **GIVEN** the backend returns a full persisted request row with `requestKind: "realtime_live"` and `transport: "websocket"`
- **WHEN** the dashboard parses the response
- **THEN** parsing succeeds with the typed `realtime_live` value preserved

### Requirement: Private realtime compatibility preserves zero-config base behavior and public boundaries

The capability MUST preserve existing Key configuration and registration. OAuth Live policy MUST default to inactive. It MUST add no dependency, dashboard navigation item, README section, `.env.example` line, public model entry, public `/v1/realtime/calls`, or public `/v1/realtime/client_secrets` implementation. Its user documentation MUST identify these routes as private Codex app compatibility rather than advertise a public Realtime API. The base proxy and dashboard MUST continue to start and operate with zero new setup.

#### Scenario: operator does not use Live Voice

- **WHEN** an operator starts the base proxy and dashboard without adding configuration for this capability
- **THEN** existing startup and ordinary proxy/dashboard behavior remain available
- **AND** no public model or documented public Realtime route advertises this private transport

### Requirement: Explicit first-party client routing remains documented

Documentation SHALL present two complete Codex client profiles. The built-in OAuth profile SHALL retain `model_provider = "openai"`. The registered-Key profile SHALL retain `requires_openai_auth = true` with `env_key = "CODEX_LB_API_KEY"`. Both profiles SHALL identify `experimental_realtime_webrtc_call_base_url` for `/backend-api/codex` and `experimental_realtime_ws_base_url` for `/v1`. An inactive global OAuth policy SHALL leave ordinary proxy, dashboard, and Key Live behavior available.

#### Scenario: Operator configures either supported Codex profile

- **WHEN** an operator follows the built-in OAuth profile or the registered-Key profile
- **THEN** ordinary conversations use the selected provider contract
- **AND** call creation and sideband both route through codex-lb
- **AND** the OAuth Live policy controls only callers admitted through the existing zero-key origin boundary
