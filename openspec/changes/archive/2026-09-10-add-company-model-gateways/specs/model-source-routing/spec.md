## ADDED Requirements
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
