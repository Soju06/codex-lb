# responses-api-compat delta

## MODIFIED Requirements

### Requirement: Responses input images bypass the HTTP bridge

The service MUST bypass the HTTP responses bridge when a `/v1/responses`,
`/backend-api/codex/responses`, `/responses/compact`, or `/v1/responses/compact`
request contains any `input_image` part in top-level input items, nested
message content, or tool output content, and send the request over the raw
(non-bridge) Responses stream path. This bypass MUST happen after rejecting
unsupported uploaded-image references and MUST be limited to the current
request; subsequent text-only requests MAY continue using the HTTP responses
bridge.

The raw (non-bridge) path is the source of truth for image validation and
upstream image error semantics. The bridge MUST NOT hold image requests waiting
for `response.created` when upstream rejects an invalid inline image payload.

This bridge bypass MUST NOT by itself pin the upstream stream transport. The
upstream transport for a bypassed image request MUST be resolved by the ordinary
upstream-transport precedence.

#### Scenario: Nested input_image bypasses bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains a nested content part with `type = "input_image"`
- **THEN** the request is sent through the raw (non-bridge) stream path
- **AND** the HTTP responses bridge is not used for that request

#### Scenario: Image bypass does not disable future text bridge use

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** an image-bearing request bypasses the bridge
- **THEN** the bypass applies only to that request
- **AND** a later text-only request can still use the HTTP responses bridge

#### Scenario: Image bypass does not pin the upstream transport

- **GIVEN** the HTTP responses bridge is enabled
- **AND** `upstream_stream_transport` is `"auto"`
- **WHEN** a Responses request carrying an inline `data:` image below the
  WebSocket frame budget bypasses the bridge
- **THEN** the request MUST NOT be forced onto upstream HTTP
- **AND** the configured transport policy MUST decide its upstream transport

### Requirement: Downstream-HTTP upstream transport follows a configurable policy

When a downstream HTTP/SSE request (`request_transport == "http"`) resolves its base upstream transport to `"websocket"`, the proxy MUST decide the final upstream transport using the configured `http_downstream_transport_policy`, after all higher-precedence rails have been applied, and the policy MUST NOT affect native WebSocket clients (`request_transport == "websocket"`), which keep their dedicated upstream WebSocket path.

Precedence (highest first), evaluated before the policy:

1. Outside the existing recent upstream WS failure cooldown, an explicit
   `upstream_stream_transport` override of `"http"` or `"websocket"` wins.
2. Oversized-payload bypass and the `image_generation` bypass force upstream
   HTTP. A request carrying `input_image` parts forces upstream HTTP only when
   its serialized payload exceeds the WebSocket frame budget, or when the
   payload still carries an external `http(s)` image URL that the proxy may be
   unable to inline; an inline `data:` image alone MUST NOT force upstream HTTP.
   These two residual `input_image` pins are deliberately evaluated ahead of an
   explicit `"websocket"` override wherever the request passes through the HTTP
   bridge routing decision — every `/v1/responses` and
   `/backend-api/codex/responses` request does — because that override
   short-circuits the size gate and an oversized image payload would otherwise
   fail locally with `400 payload_too_large`. A request that never reaches that
   decision, such as a `/v1/chat/completions` request whose bridge admission has
   already declined the bridge, follows item 1 instead.
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
- **WHEN** a single-shot downstream HTTP request with no sticky signals, and
  which trips none of the precedence item 2 bypasses, resolves the upstream
  transport under any policy
- **THEN** the explicit override MUST win and the request MUST use
  upstream WebSocket

#### Scenario: external image URL still forces HTTP under an explicit websocket override

- **GIVEN** `upstream_stream_transport` is explicitly `"websocket"`
- **AND** a request passing through the HTTP bridge routing decision carries an
  `input_image` part whose `image_url` is an external `http(s)` URL
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`, because the
  override would otherwise short-circuit the residual pin and hand the upstream
  WebSocket a URL it does not accept

#### Scenario: oversized payload bypass still forces HTTP under always_websocket

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
- **AND** the serialized request payload exceeds the WebSocket frame
  budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`, because the
  oversized-payload bypass has higher precedence than the policy

#### Scenario: inline image alone does not force HTTP under always_websocket

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
- **AND** a request carries an inline `data:` image below the WebSocket
  frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket

#### Scenario: external image URL still forces HTTP

- **GIVEN** `upstream_stream_transport` is `"auto"`
- **AND** a request carries an `input_image` part whose `image_url` is an
  external `http(s)` URL, anywhere in the input — including inside a
  tool-output array, which the image inliner never rewrites
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: native WebSocket clients are unaffected by the policy

- **GIVEN** any value of `http_downstream_transport_policy`
- **WHEN** a native WebSocket client (`request_transport == "websocket"`)
  streams a request
- **THEN** the client MUST keep its dedicated upstream WebSocket path and
  the policy MUST NOT downgrade it to HTTP
