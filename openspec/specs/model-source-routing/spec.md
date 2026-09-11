# Model Source Routing Specification

## Purpose

Define capability-based routing and accounting for OpenAI-compatible model sources, including field-preserving embeddings forwarding.
## Requirements
### Requirement: Model sources declare an embeddings capability

Each model source MUST carry a persisted `supports_embeddings` boolean
capability flag. The flag MUST default to disabled, so a source created or
migrated without an explicit value MUST NOT be treated as embeddings-capable.
The model-source create, read, and update contracts MUST expose the flag, and
the stored value MUST survive a round trip through those contracts.

#### Scenario: existing sources default to disabled

- **GIVEN** a model source row that predates the embeddings capability
- **WHEN** the schema migration runs
- **THEN** the source reports `supports_embeddings` as disabled
- **AND** its existing chat-completions, responses, and audio-transcription
  routing is unchanged

#### Scenario: capability round-trips through the API

- **WHEN** a client creates or updates a model source with the embeddings
  capability enabled
- **THEN** reading the source back reports the capability as enabled

#### Scenario: omitted capability parses as disabled

- **WHEN** a model-source payload omits `supports_embeddings`
- **THEN** it parses as disabled rather than failing validation

### Requirement: Embeddings route only to capable model sources

The system SHALL expose `POST /v1/embeddings` and MUST serve it only from an
enabled model source of kind `openai_compatible` that declares the embeddings
capability and has the requested model enabled. Embeddings requests MUST NOT
fall back to subscription-backed accounts. When the caller presents an API key
restricted to a set of sources, selection MUST stay inside that set. Beyond
the validated `model` and `input` fields, the request payload MUST be
forwarded to the source verbatim.

#### Scenario: capable source serves the request

- **GIVEN** an enabled model source declaring the embeddings capability with
  the requested model enabled
- **WHEN** a client posts to `/v1/embeddings`
- **THEN** the proxy forwards the payload to that source's `/embeddings`
  endpoint and returns the upstream JSON response

#### Scenario: no capable source is a model error

- **GIVEN** no enabled model source declares the embeddings capability for
  the requested model
- **WHEN** a client posts to `/v1/embeddings`
- **THEN** the proxy returns 404 with an OpenAI-format error envelope using
  code `model_not_found`
- **AND** the request is not routed to a subscription-backed account

#### Scenario: source-restricted API key cannot escape its set

- **GIVEN** an API key restricted to a set of model sources
- **WHEN** the only embeddings-capable source for the model is outside that
  set
- **THEN** the proxy returns `model_not_found`

### Requirement: Embeddings requests are accounted like other source routes

Embeddings responses MUST be inspected for prompt and total token usage. When
the caller's API key requires usage for settlement and the source response
reports none, the proxy MUST fail closed with `usage_unavailable` rather than
serving unmetered traffic. Every embeddings attempt that is dispatched to a
model source MUST produce a request-log entry, with `success` on a forwarded
response and `error` on a forwarding, usage, or settlement failure. That entry
MUST carry the upstream status code when a source returned an HTTP response,
and MUST record the upstream status as absent when the attempt failed before
any response was received. A request rejected before source selection succeeds
is not a dispatched attempt: it MUST NOT produce a request-log entry, because
no source was contacted and no reservation was consumed.

#### Scenario: missing usage fails closed for a limited key

- **GIVEN** an API key whose reservation requires reported usage
- **WHEN** the model source returns an embeddings response without a usage
  object
- **THEN** the proxy returns an error envelope using code `usage_unavailable`
- **AND** records an error request log

#### Scenario: forwarding error propagates the upstream status

- **WHEN** the model source returns an error status for an embeddings request
- **THEN** the proxy returns an OpenAI-format error envelope with that status
- **AND** records an error request log carrying the upstream status code

#### Scenario: transport failure records an attempt without an upstream status

- **WHEN** the request to the model source fails before any HTTP response is
  received
- **THEN** the proxy records an error request log for the attempt with no
  upstream status code

#### Scenario: unroutable model is not a logged attempt

- **GIVEN** no enabled model source declares the embeddings capability for
  the requested model
- **WHEN** a client posts to `/v1/embeddings`
- **THEN** the proxy returns the `model_not_found` envelope without writing a
  request-log entry
- **AND** no reservation is consumed for the rejected request

### Requirement: Embeddings source forwarding preserves field presence

For source-routed `POST /v1/embeddings` requests, the system MUST preserve both
the values and presence of fields beyond the validated `model` and `input`
fields. A field explicitly supplied as null MUST be forwarded as null, a field
omitted by the client MUST remain absent, and a non-null field MUST be forwarded
unchanged. This forwarding behavior MUST NOT change reservation settlement or
request-log metadata.

#### Scenario: explicit null extras remain present

- **WHEN** a client supplies `dimensions: null` and `user: null` in a
  source-routed embeddings request
- **THEN** the compatible source receives both keys with null values

#### Scenario: omitted extras remain absent

- **WHEN** a client omits `dimensions` and `user` from a source-routed
  embeddings request
- **THEN** the compatible source payload does not contain either key

#### Scenario: non-null extras and accounting remain unchanged

- **WHEN** a client supplies non-null embedding extras through a limited API
  key
- **THEN** the compatible source receives those values unchanged
- **AND** the reservation settles from reported usage
- **AND** the successful request log retains its model-source metadata and
  token counts

### Requirement: Owner-unavailable stream health preserves the recovery cause

The service SHALL use the original upstream error code for account-health
recovery when a Responses stream rewrites an upstream failure to
`previous_response_owner_unavailable`. The rewrite MUST NOT change
source-ownership selection, owner pinning, or stale-anchor matching.

#### Scenario: Owner-unavailable rewrite records original recovery code

- **WHEN** an upstream Responses failure with an account-recovery code is
  rewritten to `previous_response_owner_unavailable`
- **THEN** account health receives the original upstream code
- **AND** source ownership and stale-anchor classification remain unchanged

### Requirement: Explicit local LLMBox authentication
A source created with kind `llmbox` SHALL be disabled initially and SHALL use only the fixed `https://llmbox.bytedance.net/v1` endpoint. It MUST NOT accept a supplied API key or route local credentials to another origin. Source kind SHALL be immutable. Each dispatched request SHALL read the current server user's LLMBox login cache; missing or malformed credentials MUST fail before contacting upstream, without starting login or falling back to another identity. The local token MUST NOT appear in API responses or forwarded upstream HTTP error bodies.

#### Scenario: Create an opt-in source
- **WHEN** an operator creates a LLMBox source
- **THEN** its kind is llmbox and it is disabled until explicitly enabled
- **AND** existing sources retain their previous behavior

#### Scenario: Credentials rotate
- **WHEN** the local login cache changes between requests
- **THEN** the next request uses the updated token without restarting the service

#### Scenario: Destination changes
- **WHEN** an operator changes a LLMBox source to another base URL or supplies an API key
- **THEN** the update is rejected

### Requirement: Native Responses routing
An enabled LLMBox source SHALL route only explicitly configured models and capabilities through the existing native Responses lifecycle. This feature MUST NOT configure account-pool overflow or relabel a dynamic model as a fixed model. Embeddings and audio SHALL remain unsupported for this source kind until separately verified.

#### Scenario: Stream a tool call
- **WHEN** an explicitly selected LLMBox model emits Responses function-call events
- **THEN** the proxy preserves the call ID, arguments and terminal event for tool-result continuation

### Requirement: Honest company quota presentation
The model-source dashboard SHALL distinguish credential-cache presence from authenticated upstream health. It SHALL show upstream remaining quota and reset time as unknown when no authoritative quota interface has been verified. Local usage SHALL be labelled as observations from retained request logs over the last 24 hours, with known token totals and requests missing complete token usage reported separately. Missing usage MUST NOT be presented as zero consumption or used to infer remaining quota.

#### Scenario: No quota API
- **WHEN** a LLMBox source is displayed
- **THEN** remaining quota and reset time are unknown and no quota percentage is fabricated
- **AND** local observed usage is displayed separately

### Requirement: Company operational admission
Company model sources SHALL derive health from persisted request outcomes. Client cancellations and ordinary request validation failures SHALL NOT count as source faults. A 429 or authentication failure SHALL cause a 60-second cooldown; three consecutive qualifying transport/server failures SHALL cause a 60-second cooldown. After cooldown expires, requests SHALL be allowed to establish recovery. Successful requests SHALL restore healthy state. Admission rejection SHALL NOT route to subscription accounts or extend cooldown.

#### Scenario: Rate limit recovery
- **WHEN** a company source returns 429
- **THEN** subsequent calls during cooldown receive a source-specific 503 with Retry-After, and calls after cooldown can reach the source

### Requirement: Local observed token budget
Company sources SHALL support an optional persisted rolling 24-hour token budget, defaulting to unlimited. Once observed input plus output tokens reach the budget, new Responses and Chat calls SHALL be rejected with 429. The dashboard SHALL distinguish this local soft budget from unknown upstream remaining quota and display missing usage records. Concurrent in-flight requests and missing upstream usage MAY exceed this soft budget.

#### Scenario: Budget exhaustion
- **WHEN** recorded tokens in the previous 24 hours reach the configured budget
- **THEN** a new request is rejected before forwarding and clearing the budget restores admission

### Requirement: Company operational visibility
The dashboard SHALL display observed health, cooldown expiry, 24-hour successes, failures, 429 and server errors, measured latency when available, and editable local token budget. Unknown health SHALL remain distinct from healthy. State SHALL survive process restart through persisted request logs and budget configuration.

#### Scenario: No observations
- **WHEN** a source has no qualifying completed requests
- **THEN** its health is unknown and its upstream quota remains unknown

### Requirement: Explicit TRAE model source
The system SHALL support a disabled-by-default TRAE model source bound to its fixed company HTTPS gateway and the server-local TRAE login. It SHALL reject supplied API keys, arbitrary destinations and unimplemented modalities, and SHALL NOT transmit local credentials through redirects.
#### Scenario: Operator adds TRAE
- **WHEN** the operator creates a TRAE source
- **THEN** the source is disabled until enabled explicitly and exposes credential availability without the credential value

### Requirement: TRAE Responses translation
The system SHALL translate supported Responses inputs and TRAE streaming events without silently dropping unsupported input, tool results, reasoning continuity or terminal errors. Queue frames SHALL NOT be treated as inference completion. Connections and source reservations SHALL be released on timeout or client disconnect.
#### Scenario: Tool execution continues
- **WHEN** Codex returns a tool result with its preceding call context
- **THEN** the adapter preserves the call identifier and tool name and sends the tool output in the corresponding TRAE tool message
#### Scenario: Upstream fails after HTTP success
- **WHEN** TRAE emits an error or closes without a terminal result
- **THEN** the client receives an explicit failure and the request is not reported as completed

### Requirement: Honest company model availability
The system SHALL distinguish discovered models from verified inference and end-to-end integrations. It SHALL NOT report unknown quota as unlimited or infer a fixed backing model for dynamic aliases. Operator model catalogs SHALL exclude GLM for this deployment and preserve the identity of each configured upstream model.
#### Scenario: No authoritative quota API
- **WHEN** upstream provides no validated remaining-quota contract
- **THEN** remaining quota is unknown and observed request usage is displayed separately

### Requirement: Explicit Codebase native gateway
The system SHALL support a disabled-by-default Codebase LLMProxy source bound to its fixed HTTPS Model gateway and the server-local Codebase-backed TRAE login. It SHALL reject supplied API keys, arbitrary destinations, unsupported authentication schemes and redirects. Native Chat Completions and Responses capabilities SHALL be configured explicitly; an available Chat endpoint SHALL NOT alone be advertised as Codex Responses compatibility.
#### Scenario: Native model forwarding
- **WHEN** an enabled native source receives a request for a configured model and protocol
- **THEN** it forwards the exact model using the local credential and records observed usage separately from unknown quota
#### Scenario: Unsupported credential
- **WHEN** the local login is not a Codebase-backed credential
- **THEN** the source reports unavailable credentials and fails without making an upstream inference request
#### Scenario: Native model collides with a subscription model
- **WHEN** the operator configures a native model as `codebase/<upstream-id>`
- **THEN** source routing uses that namespaced identifier and the native gateway receives exactly `<upstream-id>` without switching to another model

### Requirement: Explicit Chat backend for Responses clients
The system SHALL translate Responses requests for native models explicitly configured with a Chat backend into Chat Completions. It SHALL preserve tool identifiers, multiple calls, custom-tool input and encrypted reasoning continuation bound to the same source and model. Unsupported inputs SHALL fail explicitly. It SHALL translate text, tools, token usage and terminal errors into Responses events, and SHALL NOT treat EOF or an upstream error as successful completion.
#### Scenario: Native Chat tool continuation
- **WHEN** Codex replays output items and tool results for a Chat-backed native model
- **THEN** the source sends matching assistant calls and tool-result messages, including decrypted reasoning state, to the exact configured model
#### Scenario: Truncated native Chat stream
- **WHEN** a stream closes without a recognized finish reason and terminal marker
- **THEN** the Responses client receives a failure rather than a completed response
#### Scenario: Company source presets
- **WHEN** an operator adds Codebase / Coco or LLMBox using a preset
- **THEN** only explicitly verified model IDs are offered, model rows start disabled, and the LLMBox preset excludes dynamic auto aliases that cannot guarantee the no-GLM policy

### Requirement: Company WebSocket bridge
Both Responses WebSocket endpoints SHALL serve company source response.create frames through the existing HTTP source policy and forwarding path. They SHALL relay complete SSE JSON events as WebSocket messages, enforce authentication, budgets and source concurrency, and SHALL NOT fall back to subscription accounts. Disconnect or cancellation SHALL terminate the owned HTTP invocation and release its resources.

#### Scenario: Desktop company request
- **WHEN** a client requests an enabled company model over WebSocket
- **THEN** source response events reach the client without requiring HTTP fallback and the request is accounted once against the company source

### Requirement: Bounded company WebSocket continuation
The bridge SHALL retain only the latest successful response context per connection, bounded by the configured request frame byte limit. A matching previous_response_id on the same model SHALL expand input plus output plus the new input. Unknown or cross-model anchors SHALL fail explicitly. No failed or cancelled response SHALL establish a continuation anchor. Pipelined frames SHALL be bounded and unsupported control frames SHALL receive explicit errors.

#### Scenario: Tool result continuation
- **WHEN** a subsequent create references the latest successful response and includes its tool result
- **THEN** the upstream receives the original input, tool call and matching tool result in order

### Requirement: Company catalogs reflect observed usability
The service SHALL run a minimal inference health check for every enabled company model every two hours. Each check SHALL have a 30-second deadline and SHALL record its model, outcome, latency, usage and completion time separately from user traffic. Only the elected scheduler leader SHALL issue probes. Client model catalogs SHALL advertise a company model only when its latest scheduled check succeeded within the freshness window and completed within 30 seconds. User-request outcomes SHALL NOT determine catalog visibility. Source credential unavailability or exhausted local budget SHALL hide all models belonging to that source. Configured model settings and direct explicit requests SHALL remain available. Catalog reads SHALL NOT perform inference probes.

#### Scenario: Model recovery
- **WHEN** a hidden company model has a later qualifying scheduled health check
- **THEN** the next catalog fetch includes that model subject to source admission state

#### Scenario: Source and model isolation
- **WHEN** one model fails upstream and its sibling has a qualifying success
- **THEN** only the failed model is hidden unless shared source admission blocks both

#### Scenario: Unknown and stale models
- **WHEN** a company model has no fresh successful scheduled health check
- **THEN** both client catalog formats omit that model without deleting its configuration

#### Scenario: User traffic does not change visibility
- **WHEN** a user request succeeds, fails, times out, or contains unsupported input
- **THEN** the catalog continues to use the latest scheduled health-check result

