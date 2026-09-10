## ADDED Requirements

### Requirement: Structured HTTP continuation promotion
Under automatic upstream transport and smart HTTP policy, the proxy SHALL
recognize a non-empty conversation identifier, a tool-result input item, or an
assistant response followed by new user input as continuation evidence, in
addition to existing response, cache, session and turn-state identifiers.
Tool declarations, instruction messages, and multiple user-only messages SHALL
NOT alone constitute continuation evidence.

#### Scenario: Full-history agent turn
- **WHEN** a smart HTTP request contains user, assistant, then user input without explicit continuity metadata
- **THEN** it is eligible for the upstream WS bridge
- **AND** the complete input remains intact unless existing verified hard-continuity rules authorize trimming

#### Scenario: Native identity without failure evidence
- **WHEN** a native Codex HTTP request has continuation evidence and upstream WS is healthy
- **THEN** the same smart/override policy as other HTTP requests applies
- **AND** native identity alone MUST NOT force HTTP

#### Scenario: Real upstream outage
- **WHEN** the existing recent upstream WS failure marker is active
- **THEN** HTTP entry paths MUST use upstream HTTP during its existing cooldown
- **AND** normal WS eligibility MUST return when it expires or clears

### Requirement: Inferred continuation locality remains soft
History-only bridge requests SHALL use a deterministic locality key based on
complete initial user input and instruction context, isolated by API-key scope.
Conversation identifiers SHALL have distinct locality from inferred histories.
Inferred locality SHALL NOT authorize previous-response injection, cross-account
replay, or dropping client history. Explicit turn/session ownership SHALL retain
precedence and existing recovery/fork/queue safeguards.

#### Scenario: Repeated history without response headers
- **WHEN** two compatible multi-turn requests retain the same initial user input and instructions
- **THEN** they can reuse the same healthy upstream connection without replaying response headers
- **AND** divergent complete initial inputs MUST NOT share an inferred locality key merely because their first 512 characters match

### Requirement: Chat Completions uses HTTP bridge policy
Subscription-backed Chat Completions SHALL apply the same HTTP bridge admission
and fallback policy to its converted Responses request for streaming and
non-streaming clients. Chat chunks, JSON, usage, error envelopes, reservation
settlement and source routing SHALL preserve their existing public contracts.

#### Scenario: Chat trailing slash uses the same handler
- **WHEN** a client posts to `/v1/chat/completions/`
- **THEN** the same authentication, routing, bridge and response contract as `/v1/chat/completions` SHALL apply

#### Scenario: Chat tool loop reuses upstream connection
- **WHEN** successive Chat requests include tool results and retain compatible initial context
- **THEN** eligible requests reuse the upstream WS bridge
- **AND** clients still receive Chat Completions responses

#### Scenario: Chat bridge fails before settlement handoff
- **WHEN** bridge startup fails or is cancelled before dispatch or service settlement ownership
- **THEN** the originating API releases its usage reservation
- **AND** an ambiguous owner-forward dispatch MUST NOT release the reservation without a definitive rejection

### Requirement: HTTP bridge routing reasons are observable
The proxy SHALL emit structured admission and bypass reasons and bounded-label
counters, and SHALL count bridge create/reuse/close/reconnect/idle-eviction events.
Metrics SHALL NOT label raw request, conversation, session, account or API-key identifiers.

#### Scenario: Identify policy exclusion and transport fallback
- **WHEN** a request remains HTTP because it is single-turn, policy-pinned, bridge-disabled, oversized, image-capable, or affected by a recent WS outage
- **THEN** its routing diagnostics distinguish that reason
- **AND** admission counters MUST NOT be represented as successful WS connections

## MODIFIED Requirements

### Requirement: Downstream-HTTP upstream transport follows a configurable policy

When a downstream HTTP/SSE request (`request_transport == "http"`) resolves its base upstream transport to `"websocket"`, the proxy MUST decide the final upstream transport using the configured `http_downstream_transport_policy`, after all higher-precedence rails have been applied, and the policy MUST NOT affect native WebSocket clients (`request_transport == "websocket"`), which keep their dedicated upstream WebSocket path.

Precedence (highest first), evaluated before the policy:

1. Outside the existing recent upstream WS failure cooldown, an explicit
   `upstream_stream_transport` override of `"http"` or `"websocket"` wins.
2. Oversized-payload bypass and image / image-generation bypass force
   upstream HTTP.
3. The effective policy (per-API-key `transport_policy_override` when
   set, otherwise the global `http_downstream_transport_policy`) decides.

Policy values and behavior:

- `always_http` (and its alias `pinned`): the request MUST be sent over
  upstream HTTP `POST`, preserving the legacy unconditional pin.
- `always_websocket`: the request MUST keep upstream WebSocket whenever
  the base transport resolved to `"websocket"` without replacing a base
  `"auto"` transport mode with a hard `"websocket"` override.
- `smart` (default): the request MUST keep upstream WebSocket **iff** at
  least one sticky-continuation signal is present on the request, and
  MUST otherwise fall back to upstream HTTP. The sticky-continuation
  signals are:
  - a non-null `previous_response_id` on the request payload, **OR**
  - a `prompt_cache_key` present on the request model, **OR**
  - a Codex session header (`session_id`, `x-codex-session-id`, or
    `x-codex-conversation-id`), **OR**
  - an `x-codex-turn-state` continuity header, **OR**
  - a non-empty `conversation` identifier, **OR**
  - a structured tool-result input item (`function_call_output`,
    `custom_tool_call_output`, or `apply_patch_call_output`), **OR**
  - an assistant message followed by new user input in the supplied history.

Tool declarations, instruction messages, and user-only input sequences MUST NOT
alone count as continuation evidence. The existing recent upstream WS failure
cooldown MUST force upstream HTTP before these policy choices, including an
explicit WebSocket preference; clearing or expiring the marker restores normal
eligibility.

When a policy decision keeps upstream WebSocket, the proxy MUST preserve
the configured/base downstream transport mode passed to the upstream
client. In particular, a base `"auto"` mode MUST remain `"auto"` so the
existing WebSocket-handshake rejection fallback to upstream HTTP remains
available. The policy MAY force a concrete transport override only when
the decision is to downgrade to upstream HTTP.

The per-API-key `transport_policy_override`, when non-null, MUST be used
as the effective policy for requests authenticated by that key and MUST
take precedence over the global default. A null override MUST fall
through to the global `http_downstream_transport_policy`.

#### Scenario: single-shot downstream-HTTP request falls back to HTTP under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport resolves to `"websocket"`
- **AND** a downstream HTTP request carries no `previous_response_id`, no
  `prompt_cache_key`, no Codex session header, and no `x-codex-turn-state`
  header, conversation identifier, tool result, or assistant-to-user history
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: sticky downstream-HTTP request keeps WebSocket under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport mode is `"auto"` and resolves to `"websocket"`
- **AND** a downstream HTTP request carries any one of
  `previous_response_id`, `prompt_cache_key`, a Codex session header, or
  an `x-codex-turn-state` header
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`
- **AND** an upstream WebSocket handshake rejection status eligible for
  auto fallback MUST transparently retry over upstream HTTP

#### Scenario: always_http policy preserves the legacy pin

- **GIVEN** `http_downstream_transport_policy` is `"always_http"` (or
  `"pinned"`) and the base upstream transport resolves to `"websocket"`
- **WHEN** a downstream HTTP request resolves the upstream transport,
  regardless of sticky signals
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: always_websocket policy never downgrades sticky-less HTTP

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
  and the base upstream transport mode is `"auto"` and resolves to
  `"websocket"`
- **WHEN** a downstream HTTP request with no sticky signals resolves the
  upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`

#### Scenario: per-key override wins over the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has
  `transport_policy_override = "always_http"`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`,
  because the per-key override takes precedence

#### Scenario: null per-key override follows the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has `transport_policy_override =
  null`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket, following the global
  `smart` policy

#### Scenario: explicit websocket override still beats the policy

- **GIVEN** `upstream_stream_transport` is explicitly `"websocket"`
- **AND** no recent upstream WS failure marker is active
- **WHEN** a single-shot downstream HTTP request with no sticky signals
  resolves the upstream transport under any policy
- **THEN** the explicit override MUST win and the request MUST use
  upstream WebSocket

#### Scenario: oversized payload bypass still forces HTTP under always_websocket

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
- **AND** the serialized request payload exceeds the WebSocket frame
  budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`, because the
  oversized-payload bypass has higher precedence than the policy

#### Scenario: native WebSocket clients are unaffected by the policy

- **GIVEN** any value of `http_downstream_transport_policy`
- **WHEN** a native WebSocket client (`request_transport == "websocket"`)
  streams a request
- **THEN** the client MUST keep its dedicated upstream WebSocket path and
  the policy MUST NOT downgrade it to HTTP

### Requirement: HTTP session bridge admission obeys downstream transport policy

Before an HTTP/SSE Responses request enters the upstream WebSocket session bridge, the proxy MUST apply the same explicit-transport precedence and effective `http_downstream_transport_policy` used by the ordinary streaming retry path. Outside the existing recent upstream WS failure cooldown, an explicit upstream `http` selection MUST bypass the bridge, an explicit upstream `websocket` selection MUST retain it, and otherwise the per-key override or global policy MUST decide. A bridge bypass MUST continue through the ordinary HTTP streaming path without changing request or response shapes.

#### Scenario: Always-HTTP bypasses an enabled bridge

- **GIVEN** the HTTP Responses session bridge is enabled
- **AND** the effective downstream-HTTP policy is `always_http` or `pinned`
- **WHEN** a downstream HTTP/SSE request is handled
- **THEN** the request bypasses the WebSocket session bridge
- **AND** is sent through the ordinary upstream HTTP path

#### Scenario: Smart bridge admission follows continuity signals

- **GIVEN** the HTTP Responses session bridge is enabled
- **AND** the effective policy is `smart`
- **WHEN** a request has no sticky-continuation signal
- **THEN** it bypasses the bridge
- **BUT WHEN** any defined sticky-continuation signal is present
- **THEN** it remains eligible for the bridge

#### Scenario: Explicit transport wins before bridge admission

- **GIVEN** the HTTP Responses session bridge is enabled and no recent upstream WS failure marker is active
- **WHEN** upstream transport is explicitly `http`
- **THEN** the bridge is bypassed under every policy
- **BUT WHEN** upstream transport is explicitly `websocket`
- **THEN** the bridge remains enabled under every policy

#### Scenario: Per-key policy controls bridge admission

- **GIVEN** a per-API-key transport policy override is non-null
- **WHEN** bridge admission is evaluated
- **THEN** that override is used instead of the global policy

## RENAMED Requirements
- FROM: `### Requirement: Native Codex HTTP attempts preserve client transport choice`
- TO: `### Requirement: Native Codex HTTP attempts preserve verified transport fallback`

## MODIFIED Requirements

### Requirement: Native Codex HTTP attempts preserve verified transport fallback

Native Codex HTTP/SSE requests SHALL follow the same effective HTTP transport
policy as other HTTP requests. First-party User-Agent or originator identity
alone MUST NOT imply a previous WS failure or force upstream HTTP. The existing
recent upstream WS connect-failure marker and explicit upstream HTTP preference
MUST preserve HTTP fallback. Native downstream WebSocket requests MUST remain
on their dedicated WebSocket path.

#### Scenario: Healthy native HTTP continuation is promoted
- **GIVEN** a native Codex HTTP request carries continuation evidence
- **WHEN** upstream transport is automatic, HTTP policy is smart, and no recent WS failure is active
- **THEN** the request is eligible for the upstream WS bridge

#### Scenario: Verified Codex HTTP fallback is retained during outage
- **GIVEN** the existing recent upstream WS connect-failure marker is active
- **WHEN** a native Codex HTTP request arrives
- **THEN** codex-lb sends the attempt upstream over HTTP
- **AND** normal promotion eligibility returns when the marker clears or expires

#### Scenario: Explicit WebSocket remains authoritative when healthy
- **GIVEN** a native Codex HTTP request with no active WS transport failure marker
- **WHEN** the operator explicitly configures upstream WebSocket transport
- **THEN** the explicit WebSocket selection remains authoritative
