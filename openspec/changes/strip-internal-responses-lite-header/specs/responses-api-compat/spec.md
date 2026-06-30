## ADDED Requirements

### Requirement: Internal Responses Lite header is not forwarded upstream

The service MUST accept inbound Responses and compact requests that include
`X-OpenAI-Internal-Codex-Responses-Lite`, but MUST remove that header before
calling upstream Responses, compact, or websocket transports. Header matching
MUST be case-insensitive. The service MUST NOT strip unrelated OpenAI SDK
telemetry headers solely because they start with `x-openai-`.

#### Scenario: HTTP and compact upstream headers omit Lite

- **WHEN** a client sends a Responses or compact request with
  `X-OpenAI-Internal-Codex-Responses-Lite: 1`
- **THEN** the upstream HTTP request headers omit
  `x-openai-internal-codex-responses-lite`
- **AND** unrelated headers such as `x-openai-client-version` continue through
  the existing fingerprint policy

#### Scenario: Websocket upstream headers omit Lite

- **WHEN** a client opens a Responses websocket with
  `X-OpenAI-Internal-Codex-Responses-Lite: 1`
- **THEN** the upstream websocket connection headers omit
  `x-openai-internal-codex-responses-lite`
- **AND** existing websocket beta and Codex continuity headers are preserved
