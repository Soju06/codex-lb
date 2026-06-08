## ADDED Requirements

### Requirement: Agent provider registry
The system SHALL expose a typed agent-provider registry that distinguishes
Codex and Gemini provider state. Provider metadata SHALL include provider id,
display name, lifecycle status, protocol surfaces, auth modes, quota dimensions,
dashboard sections, lifecycle notes, operator action, and optional availability
cutover date.

#### Scenario: Codex provider remains production-ready
- **WHEN** provider metadata is requested
- **THEN** the Codex provider is reported as `ready`
- **AND** its protocol surfaces include the existing Codex/ChatGPT route surface

#### Scenario: Gemini provider is visible before routing is enabled
- **WHEN** provider metadata is requested
- **THEN** the Gemini provider is reported separately from Codex
- **AND** the response identifies Gemini API as proxyable foundation work
- **AND** the response does not claim Gemini request routing is production-ready
- **AND** the response includes operator guidance for creating Gemini API-key accounts

### Requirement: Provider-scoped dashboard contract
The dashboard SHALL consume provider metadata from the backend instead of
hardcoding provider availability. Provider-specific settings and account views
MUST remain scoped to their provider, and the combined overview MUST aggregate
provider summaries without sharing credentials or quota state across providers.

#### Scenario: Provider metadata API is dashboard authenticated
- **GIVEN** a remote unauthenticated client
- **WHEN** it requests provider metadata
- **THEN** the API rejects the request with the dashboard authentication error

#### Scenario: Combined overview is backend aggregated
- **WHEN** the dashboard requests the provider overview for a supported timeframe
- **THEN** the API returns Codex, Gemini, and Antigravity provider rows
- **AND** totals include provider count, account count, active account count, quota-window count, request count, success count, error count, input tokens, output tokens, and cached input tokens
- **AND** request-log aggregation maps Gemini and Antigravity source logs to their provider ids while treating existing Codex logs as Codex
- **AND** warmup requests and soft-deleted request logs are excluded from provider overview request totals

#### Scenario: Codex provider tab exposes routing controls
- **WHEN** an operator opens the Codex provider section
- **THEN** the dashboard shows Codex account health, the active Codex routing strategy, and budget threshold state
- **AND** the operator can update Codex routing strategy, selected single account, ordered-fallback account priority, and primary budget threshold from the provider dashboard
- **AND** those controls reuse the existing Codex settings contract instead of storing Codex routing in Gemini or Antigravity provider rows

### Requirement: Gemini account credentials are provider-scoped
Gemini API credentials SHALL be stored in provider-scoped account rows instead
of Codex account rows. The API MUST encrypt Gemini API keys at rest, MUST expose
only non-secret account metadata, and MUST support listing Gemini accounts
separately from Codex accounts.

#### Scenario: Gemini account creation stores no plaintext key
- **WHEN** an operator creates a Gemini provider account with an API key
- **THEN** the API response includes provider id, account id, display name,
  auth mode, fingerprint, and non-secret metadata
- **AND** the API response does not include the plaintext API key
- **AND** the persisted credential material is encrypted

#### Scenario: Gemini account update rotates encrypted credentials
- **WHEN** an operator updates a Gemini provider account with a replacement API key
- **THEN** the API stores the replacement as encrypted credential material
- **AND** the account fingerprint changes
- **AND** the API response does not include the plaintext API key
- **AND** display name, status, project, and location remain provider-scoped metadata

### Requirement: Gemini native request adapter
Gemini runtime code SHALL translate OpenAI-style chat request metadata into
Gemini native `generateContent` and `streamGenerateContent` request payloads
inside provider-owned code. The adapter MUST keep Codex proxy request
translation unchanged and MUST expose streaming output as OpenAI-compatible chat
completion chunks before it is wired into shared routing.

#### Scenario: Chat messages map to Gemini contents
- **WHEN** a Gemini chat request includes system, user, and assistant messages
- **THEN** system/developer text is mapped to Gemini `systemInstruction`
- **AND** user messages are mapped to Gemini `role = user`
- **AND** assistant messages are mapped to Gemini `role = model`

#### Scenario: Gemini SSE maps to chat chunks
- **WHEN** Gemini streaming SSE emits a JSON `data:` event with text parts
- **THEN** the adapter returns an OpenAI-compatible chat completion chunk
- **AND** the finish reason is normalized to the OpenAI chat finish reason set

#### Scenario: Gemini function calls map to OpenAI tool calls
- **WHEN** Gemini returns a `functionCall` part for a function declaration
- **THEN** the adapter returns an OpenAI-compatible assistant `tool_calls` payload
- **AND** the function name and JSON arguments are preserved for the client agent loop

#### Scenario: Gemini stream cleanup handles cancellation and split UTF-8
- **WHEN** Gemini streaming bytes split a UTF-8 code point across network chunks
- **THEN** the stream decoder preserves the character and continues parsing SSE events
- **AND** if the downstream client cancels a Gemini stream with an API-key reservation
- **THEN** the reservation is released and the cancellation is logged before propagating cancellation

#### Scenario: Standard chat completions dispatches Gemini models
- **WHEN** a client calls `/v1/chat/completions` with an effective model id that starts with `gemini-`
- **THEN** the request is routed through the Gemini provider runtime
- **AND** Codex model ids continue through the existing Codex proxy path
- **AND** provider-scoped Gemini quota settlement and request logging are applied

#### Scenario: Model discovery includes Gemini provider models
- **WHEN** a client calls `/v1/models`
- **THEN** Gemini Developer API chat models are listed with provider, protocol, lifecycle, token-limit, and capability metadata
- **AND** Codex registry models remain listed with the existing Codex metadata contract
- **AND** API-key allowed/enforced model settings filter Gemini models before returning the catalog

#### Scenario: Model discovery includes Antigravity managed-agent model
- **WHEN** a client calls `/v1/models`
- **THEN** the Antigravity managed-agent model `antigravity-preview-05-2026` is listed with provider, protocol, lifecycle, token-limit, and capability metadata
- **AND** API-key allowed/enforced model settings can include or filter the Antigravity model id

#### Scenario: Dashboard model catalog includes provider models
- **WHEN** the dashboard requests `/api/models`
- **THEN** the response includes public Codex models, Gemini Developer API models, and Antigravity managed-agent models
- **AND** provider entries identify their provider, protocol, and lifecycle

### Requirement: Provider-scoped routing preflight
Gemini routing state SHALL persist provider-owned routing settings before
selection logic reads them. Preflight SHALL use the same account status,
single-account scope, quota windows, budget threshold, and drain strategy
inputs as the future Gemini request router, and MUST NOT fallback to another
account when single-account scope or quota budget denies the selected account.

#### Scenario: Drain strategy is budget gated
- **GIVEN** one Gemini account is over the quota threshold and another remains
  inside budget
- **WHEN** reset-drain preflight runs
- **THEN** the over-budget account is excluded before drain ordering
- **AND** the selected account is one of the budget-safe candidates

#### Scenario: Single-account preflight does not opportunistically fallback
- **GIVEN** Gemini routing settings select one account
- **AND** that account is unavailable or over budget
- **WHEN** preflight runs
- **THEN** the request is denied with a provider routing reason
- **AND** no other Gemini account is selected

#### Scenario: Ordered fallback preflight honors manual priority
- **GIVEN** provider routing settings use `ordered_fallback`
- **AND** the configured ordered account list contains one over-budget account followed by one budget-safe account
- **WHEN** preflight runs
- **THEN** the budget-safe configured account is selected
- **AND** unconfigured budget-safe accounts are not used as fallback when no configured account is eligible

#### Scenario: Round-robin runtime selection advances cursor
- **GIVEN** provider routing settings use `round_robin`
- **WHEN** runtime routing selects an account for a provider request
- **THEN** the selected account id is persisted as the provider round-robin cursor
- **AND** the next round-robin selection can advance from that cursor

#### Scenario: Provider quota settlement is atomic
- **WHEN** concurrent provider requests settle usage for the same quota window
- **THEN** quota usage is incremented through an atomic database update
- **AND** one settlement cannot overwrite another settlement read from a stale ORM object

#### Scenario: Paused provider accounts are excluded from preflight
- **GIVEN** a provider account is paused from the provider dashboard
- **WHEN** provider routing preflight runs
- **THEN** the paused account is excluded from candidate selection
- **AND** the account can be resumed without changing its credential material

### Requirement: Codex manual account priority
Codex dashboard routing settings SHALL support an `ordered_fallback` strategy
that stores a deduplicated manual account priority list. The selector MUST try
eligible accounts in that order after normal availability and budget filters,
and MUST deny the request instead of falling back to an unlisted account when
the configured ordered accounts are exhausted or unavailable.

#### Scenario: Codex ordered fallback uses configured account order
- **GIVEN** multiple Codex accounts are active
- **AND** manual priority is configured as account B before account A
- **WHEN** Codex account selection runs with `ordered_fallback`
- **THEN** account B is selected before account A even if account A has lower usage

#### Scenario: Codex ordered fallback requires known accounts
- **WHEN** dashboard settings are updated to `ordered_fallback`
- **THEN** the API rejects an empty priority list
- **AND** the API rejects priority entries that are not known Codex account ids

### Requirement: Antigravity is modeled as a harness connector
Google Antigravity CLI support SHALL be tracked as a separate harness connector
surface unless an official HTTP-compatible model endpoint is configured. The
system MUST NOT route raw proxy requests to Antigravity CLI by pretending it is
equivalent to Gemini API.

#### Scenario: Antigravity is not marked proxy-ready in V1
- **WHEN** provider metadata is requested
- **THEN** Antigravity CLI appears as a foundation harness surface
- **AND** it is not marked proxyable for raw /v1 traffic
- **AND** the Gemini provider remains marked as not production-ready for routing
- **AND** its metadata identifies the 2026-06-18 Gemini CLI individual-tier cutover
- **AND** its operator action describes an `agy` harness connector rather than HTTP proxy routing

#### Scenario: Antigravity provider profile is registered without secret material
- **WHEN** an operator creates an Antigravity provider account with a display name and local `agy` profile id
- **THEN** the account is stored under provider id `antigravity`
- **AND** the auth mode is `cli_keyring`
- **AND** no API key material is stored or returned
- **AND** the dashboard lists the profile in the Antigravity section

#### Scenario: Antigravity managed-agent account stores encrypted API key
- **WHEN** an operator creates an Antigravity provider account with auth mode `api_key`
- **THEN** the API stores encrypted Gemini API-key credential material under provider id `antigravity`
- **AND** the API response exposes only non-secret metadata and fingerprint state
- **AND** the account can be selected by Antigravity managed-agent routing without being selected by the local CLI harness

#### Scenario: Antigravity profile update keeps CLI credentials local
- **WHEN** an operator updates an Antigravity profile display name, status, profile id, project, or harness location
- **THEN** the API updates only provider-scoped account metadata
- **AND** no API key material is accepted or stored for the Antigravity account
- **AND** changing the profile id updates the non-secret account fingerprint

#### Scenario: Antigravity Interactions API uses provider routing
- **GIVEN** an active Antigravity API-key provider account exists
- **AND** provider routing selects that account
- **WHEN** a proxy-authenticated client submits `/v1/antigravity/interactions`
- **THEN** the system calls the Gemini Interactions API with `Api-Revision: 2026-05-20`
- **AND** the selected provider account credential is used without exposing plaintext key material
- **AND** successful execution settles provider request usage and writes Antigravity request logs

#### Scenario: Dashboard can run Antigravity managed-agent probe
- **GIVEN** an active Antigravity API-key provider account exists
- **WHEN** an operator submits a dashboard-authenticated managed-agent run
- **THEN** the system uses Antigravity provider routing and calls the Gemini Interactions API
- **AND** the dashboard response includes the agent id, extracted output text, and raw non-secret response payload

#### Scenario: Standard chat completions dispatches Antigravity models
- **WHEN** a client calls `/v1/chat/completions` with model `antigravity-preview-05-2026`
- **THEN** the request is routed through Antigravity managed-agent provider runtime
- **AND** Codex and Gemini model ids continue through their existing provider-specific paths

#### Scenario: Antigravity harness print uses provider routing
- **GIVEN** an active Antigravity CLI profile account exists
- **AND** provider routing selects that profile
- **WHEN** an operator submits a dashboard-authenticated harness print request
- **THEN** the system runs `agy --print` noninteractively in the requested workspace
- **AND** the prompt is redacted from the returned command preview
- **AND** the dangerous permission-bypass flag is not used
- **AND** successful execution settles provider request usage for the selected profile

#### Scenario: Antigravity routing is visible in the dashboard
- **WHEN** an operator opens the Antigravity provider section
- **THEN** the dashboard shows Antigravity routing settings, quota-window controls, and preflight state
- **AND** the controls use Antigravity provider accounts rather than Gemini or Codex accounts
- **AND** the combined provider overview includes Antigravity quota windows in its provider quota total
