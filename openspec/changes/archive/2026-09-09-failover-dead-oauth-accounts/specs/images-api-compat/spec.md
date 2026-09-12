## ADDED Requirements

### Requirement: Revoked-token Images errors retain authentication status

Non-streaming image generation and edit routes, including their Codex-base aliases, MUST return HTTP 401 when an upstream terminal `response.failed` or `error` event carries `code = token_revoked` after permitted failover is exhausted. The status MUST NOT depend on an upstream authentication error type being present. The OpenAI error envelope MUST preserve the upstream error code and message.

#### Scenario: Revoked-token terminal event omits its error type

- **WHEN** a non-streaming image generation or edit request reaches an exhausted
  upstream stream with `response.failed` or `error`, `code = token_revoked`, and
  no error type
- **THEN** the canonical and Codex-base routes return HTTP 401
- **AND** the JSON error envelope preserves `token_revoked` and the upstream
  message even when the collector defaults the error type to `server_error`
