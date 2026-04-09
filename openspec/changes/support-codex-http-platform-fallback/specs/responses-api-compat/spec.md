## MODIFIED Requirements

### Requirement: Public OpenAI-compatible route eligibility is provider-aware, transport-aware, and fallback-ordered

The service MUST treat upstream execution as a provider-aware decision instead of assuming every request targets the ChatGPT-web backend. `chatgpt_web` remains primary and `openai_platform` is fallback-only. Phase-1 Platform fallback already covers HTTP `/v1/models` and stateless HTTP `/v1/responses`; this change additionally allows HTTP `/backend-api/codex/models` and stateless HTTP `/backend-api/codex/responses` to route to `openai_platform` when the selected routing subject supports the requested route family, transport, model, and required features.

#### Scenario: Backend Codex HTTP responses fall back to Platform after the ChatGPT pool is drained
- **WHEN** the operator enables `openai_platform` for `backend_codex_http`
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available for the requested model
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **AND** the request does not require websocket or payload-level continuity-dependent behavior
- **THEN** the service forwards HTTP `/backend-api/codex/responses` through the Platform transport instead of the ChatGPT-private upstream path

#### Scenario: Backend Codex HTTP model discovery falls back to Platform after the ChatGPT pool is drained
- **WHEN** the operator enables `openai_platform` for `backend_codex_http`
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **THEN** the service may satisfy HTTP `/backend-api/codex/models` from Platform model discovery translated into the backend Codex response shape

### Requirement: Continuity-dependent request shapes are gated before provider selection

The service MUST derive request capabilities from both route and request shape before it chooses an upstream routing subject. Requests are continuity-dependent when they rely on `conversation`, `previous_response_id`, explicit session headers, `x-codex-turn-state`, or downstream websocket continuity semantics. For HTTP `/backend-api/codex/responses`, downstream Codex session headers are transport hints and MUST NOT by themselves block Platform fallback in this increment; payload-level continuity fields remain unsupported.

#### Scenario: Backend Codex HTTP payload continuity request is rejected for Platform fallback
- **WHEN** a request targets HTTP `/backend-api/codex/responses`
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request includes `conversation` or `previous_response_id`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `code = "provider_continuity_unsupported"`

#### Scenario: Backend Codex HTTP session headers do not block Platform fallback
- **WHEN** a request targets HTTP `/backend-api/codex/responses`
- **AND** the allowed upstream candidates include an eligible `openai_platform` routing subject for `backend_codex_http`
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **AND** the request includes `session_id`, `x-codex-session-id`, `x-codex-conversation-id`, or `x-codex-turn-state`
- **AND** the request does not include `conversation` or `previous_response_id`
- **THEN** the service MAY route the request to Platform fallback
- **AND** those downstream session headers MUST NOT by themselves trigger `provider_continuity_unsupported`

### Requirement: Platform mode rejects unsupported backend Codex routes and features

When the selected upstream provider is `openai_platform`, the service MUST explicitly reject backend Codex routes and features that still depend on unsupported ChatGPT-private contracts until equivalent behavior is intentionally implemented and verified.

#### Scenario: Backend Codex websocket remains unsupported for Platform fallback
- **WHEN** an `openai_platform` routing subject receives `/backend-api/codex/responses` over websocket transport
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `code = "provider_transport_unsupported"`

#### Scenario: Backend Codex compact remains unsupported for Platform fallback
- **WHEN** an `openai_platform` routing subject receives `/backend-api/codex/responses/compact`
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `code = "provider_feature_unsupported"`
