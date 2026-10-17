## ADDED Requirements

### Requirement: Previous-response source routing follows proven ownership

When a Responses request targets a configured Responses-compatible model source and carries `previous_response_id`, the proxy MUST use recorded subscription-account ownership as the veto for model-source routing. The proxy MUST NOT infer ownership from the response identifier's syntax. A recorded subscription owner MUST keep the request on subscription routing. When no subscription owner is recorded, the configured model source MUST remain authoritative, including when the identifier uses the canonical OpenAI `resp_` hexadecimal shape.

For the direct Responses WebSocket transport, a recorded subscription owner MUST keep the request on the owner-bound subscription path. A configured source model without a recorded subscription owner MUST retain the existing `model_source_requires_http_transport` fallback behavior.

#### Scenario: Recorded subscription owner overrides an HTTP model source

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is not forwarded to the model source
- **AND** subscription routing preserves the recorded account owner

#### Scenario: Canonical source response ID remains source-routed over HTTP

- **GIVEN** a Responses-compatible source is configured for the requested model
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** the client calls `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the request is forwarded to the configured model source

#### Scenario: Direct WebSocket preserves a recorded subscription owner

- **GIVEN** a source is also configured for the requested model
- **AND** request logs record a subscription account as the owner of `previous_response_id`
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the request remains on the owner-bound subscription WebSocket path
- **AND** the proxy does not emit `model_source_requires_http_transport`

#### Scenario: Direct WebSocket source continuation falls back to HTTP

- **GIVEN** a source is configured for the requested model
- **AND** no subscription account is recorded as owner of `previous_response_id`
- **AND** `previous_response_id` uses a canonical OpenAI-compatible `resp_` hexadecimal shape
- **WHEN** a direct Responses WebSocket client submits the follow-up
- **THEN** the proxy emits `model_source_requires_http_transport`
- **AND** the request is not sent to a subscription upstream
