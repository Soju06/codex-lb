## ADDED Requirements

### Requirement: Request observability distinguishes accounts from model sources

Request logs and structured diagnostics for proxied requests SHALL distinguish
subscription account routing from OpenAI-compatible model-source routing. For
source-routed requests, observability MUST include a stable source id and source
kind. For subscription-routed requests, existing account id observability MUST be
preserved. Logs and request-log payloads MUST NOT include upstream source API key
material.

#### Scenario: Source-routed request records source metadata

- **WHEN** a `/v1/chat/completions` or `/v1/audio/transcriptions` request is
  routed to OpenAI-compatible source `src_local`
- **THEN** the request log or equivalent structured diagnostic records source
  kind `openai_compatible` and source id `src_local`
- **AND** `account_id` remains null unless a subscription account was actually
  used

#### Scenario: Source API key is redacted

- **WHEN** a source-routed request is logged or archived
- **THEN** the configured upstream API key is not emitted in logs, request logs,
  metrics, diagnostics, or archive metadata
