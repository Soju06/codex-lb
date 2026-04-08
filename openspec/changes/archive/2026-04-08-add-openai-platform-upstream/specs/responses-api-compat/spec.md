## ADDED Requirements

### Requirement: Public OpenAI-compatible route eligibility is provider-aware, transport-aware, and fallback-ordered

The service MUST treat upstream execution as a provider-aware decision instead of assuming every request targets the ChatGPT-web backend. In phase 1, only HTTP `/v1/models` and stateless HTTP `/v1/responses` MAY route to `openai_platform`, and only when the selected upstream routing subject supports the requested route family, transport, model, and required features. For those routes, `chatgpt_web` remains primary and `openai_platform` is fallback-only.

#### Scenario: Healthy ChatGPT-web remains primary for stateless public HTTP

- **WHEN** a request targets an eligible public HTTP route
- **AND** both `chatgpt_web` and `openai_platform` are configured for that route family
- **AND** at least one compatible ChatGPT-web candidate remains healthy under the configured primary and secondary drain thresholds
- **THEN** the request continues through the ChatGPT-web path
- **AND** the service does not switch to the Platform transport for that request

#### Scenario: HTTP `/v1/responses` falls back to an OpenAI Platform upstream after the ChatGPT pool is drained

- **WHEN** the operator enables `openai_platform` for public HTTP routes
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available for the requested model
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured primary and secondary drain thresholds
- **AND** the request does not require phase-1 unsupported continuity or websocket capabilities
- **THEN** the service forwards HTTP `/v1/responses` to the public upstream contract instead of the ChatGPT-private `/codex/responses` path

#### Scenario: Platform identity is excluded from downstream websocket route selection in phase 1

- **WHEN** a request targets downstream websocket `/responses` or `/v1/responses`
- **AND** the candidate upstream routing subject is `openai_platform`
- **THEN** the service excludes that routing subject before transport start
- **AND** if no compatible `chatgpt_web` routing subject remains it returns a stable OpenAI-format error instead of attempting a ChatGPT-shaped websocket flow on behalf of Platform mode

#### Scenario: capability mismatch fails closed

- **WHEN** routing selects or is restricted to an upstream routing subject that does not support the requested route family, transport, or feature
- **THEN** the service rejects the request with a stable OpenAI-format error
- **AND** it MUST NOT silently substitute a different upstream contract to emulate unsupported behavior

#### Scenario: Public route rejects Platform-only fallback

- **WHEN** a request targets HTTP `/v1/models` or stateless HTTP `/v1/responses`
- **AND** an `openai_platform` identity is configured for that route family
- **AND** no eligible `chatgpt_web` routing subject exists for the requested model and route
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"` and `code = "provider_fallback_requires_chatgpt"`

### Requirement: Continuity-dependent request shapes are gated before provider selection

The service MUST derive request capabilities from both route and request shape before it chooses an upstream routing subject. In phase 1, requests are continuity-dependent when they rely on `conversation`, `previous_response_id`, explicit session headers, `x-codex-turn-state`, or downstream websocket continuity semantics.

#### Scenario: Platform-backed `conversation` request is rejected in phase 1

- **WHEN** a request targets HTTP `/v1/responses`
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request includes `conversation`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and `param = "conversation"`

#### Scenario: Platform-backed `previous_response_id` request is rejected in phase 1

- **WHEN** a request targets HTTP `/v1/responses`
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request includes `previous_response_id`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and `param = "previous_response_id"`

#### Scenario: Platform-backed session-affinity headers are rejected in phase 1

- **WHEN** a request targets an OpenAI-compatible route
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request carries `session_id`, `x-codex-session-id`, `x-codex-conversation-id`, or `x-codex-turn-state`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and `param` set to the first offending continuity field name

### Requirement: Platform mode rejects phase-1 unsupported routes and features

When the selected upstream provider is `openai_platform`, the service MUST explicitly reject routes and features that still depend on ChatGPT-private or phase-gated contracts until equivalent public semantics are intentionally implemented and verified.

#### Scenario: Platform-backed compact request is rejected in phase 1

- **WHEN** an `openai_platform` routing subject receives `/v1/responses/compact` or `/backend-api/codex/responses/compact`
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_feature_unsupported"`, and no required `param`

#### Scenario: Platform-backed backend Codex route is rejected in phase 1

- **WHEN** an `openai_platform` routing subject receives any `/backend-api/codex/*` request
- **THEN** the service rejects the request instead of forwarding it to a ChatGPT-private upstream path
- **AND** it returns HTTP `400` with `type = "invalid_request_error"` and `code = "provider_feature_unsupported"`

### Requirement: Provider mismatch errors use stable codes

For provider-specific capability failures introduced by this change, the service MUST use stable OpenAI-style error envelopes and stable proxy-defined codes so tests and clients can distinguish route, transport, and continuity failures.

#### Scenario: transport mismatch returns a stable code

- **WHEN** a request is rejected because the selected provider does not support the requested downstream transport
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_transport_unsupported"`, and `param = "transport"`

#### Scenario: continuity mismatch returns a stable code

- **WHEN** a request is rejected because the selected provider does not support the required continuity behavior
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and a `param` pointing to the offending continuity field when one exists

#### Scenario: route or feature mismatch returns a stable code

- **WHEN** a request is rejected because the selected provider does not support the requested route family or feature such as compact or backend Codex
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"` and `code = "provider_feature_unsupported"`

#### Scenario: fallback prerequisite returns a stable code

- **WHEN** a request is rejected because `openai_platform` is configured but no eligible `chatgpt_web` routing subject exists for fallback
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"` and `code = "provider_fallback_requires_chatgpt"`

## MODIFIED Requirements

### Requirement: Use prompt_cache_key as OpenAI cache affinity

For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as a bounded upstream target affinity key for prompt-cache correctness. When the selected upstream provider is `chatgpt_web`, this continues to mean bounded upstream account affinity. When the selected upstream provider is `openai_platform`, it MUST preserve affinity to the selected provider-scoped routing target without implying ChatGPT-specific session continuity or widening the request's capability set.

#### Scenario: Platform prompt-cache affinity reuses the same provider target

- **WHEN** a client sends repeated stateless HTTP `/v1/responses` requests with the same non-empty `prompt_cache_key`
- **AND** the selected upstream provider is `openai_platform`
- **AND** the existing mapping is still within the configured freshness window
- **THEN** the service reuses the same provider-scoped routing target for those requests

### Requirement: HTTP Responses routes preserve upstream continuity only for providers that advertise it

When the selected upstream provider exposes durable upstream continuity for HTTP Responses routes, the service MUST preserve that continuity on a stable bridge key. When the selected upstream provider does not expose equivalent continuity semantics for the requested route family, the service MUST NOT synthesize ChatGPT-style continuity or silently open a different provider contract to satisfy the request.

#### Scenario: ChatGPT-web continuity remains unchanged

- **WHEN** the selected upstream provider is `chatgpt_web`
- **THEN** existing HTTP bridge reuse and `previous_response_id` continuity guarantees continue to apply

#### Scenario: Platform continuity-dependent request fails closed when parity is unavailable

- **WHEN** the selected upstream provider is `openai_platform`
- **AND** a request depends on provider-owned continuity semantics that are not implemented for that provider in phase 1
- **THEN** the service rejects the request with code `provider_continuity_unsupported`
- **AND** it does so before upstream transport start
- **AND** it MUST NOT create a fake ChatGPT-style bridge session on the client's behalf

#### Scenario: Platform-only public-route operation is not allowed

- **WHEN** a request targets an eligible public HTTP route
- **AND** an `openai_platform` identity exists
- **AND** there is no compatible `chatgpt_web` pool available for the deployment
- **THEN** the service MUST NOT execute the request through Platform alone
- **AND** it fails closed with code `provider_fallback_requires_chatgpt`
