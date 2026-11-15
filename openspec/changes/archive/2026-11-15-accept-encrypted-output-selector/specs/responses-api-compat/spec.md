## ADDED Requirements

### Requirement: Responses accept encrypted output inclusion

The service MUST accept `output.encrypted_content` in native and public Responses request `include` arrays over HTTP and WebSocket, and MUST forward it unchanged. It MUST preserve `reasoning.encrypted_content` alongside that selector. Existing supported selectors and omitted or empty arrays MUST retain their behavior; unknown selectors, null arrays, and non-string entries MUST remain invalid.

#### Scenario: Encrypted output is requested
- **WHEN** a client requests `include: ["reasoning.encrypted_content", "output.encrypted_content"]` through `/backend-api/codex/responses` or `/v1/responses`, using HTTP or WebSocket
- **THEN** validation succeeds and the upstream payload retains both selectors in order

#### Scenario: Invalid selectors remain rejected
- **WHEN** a request includes an unknown selector, a null include array, or a non-string entry
- **THEN** the proxy rejects the request before upstream dispatch
