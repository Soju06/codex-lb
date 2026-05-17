## ADDED Requirements

### Requirement: Accept duplicated /v1/ prefix under /backend-api/codex
Some OpenAI-compatible clients append `/v1/` to whatever the operator configured as the base URL. When the operator's base URL already terminates at `/backend-api/codex`, those clients end up requesting `/backend-api/codex/v1/<rest>` (for example `/backend-api/codex/v1/models`, `/backend-api/codex/v1/responses`, `/backend-api/codex/v1/responses/compact`).

The service MUST treat any inbound request whose path begins with `/backend-api/codex/v1/` followed by a non-empty rest as a transparent alias for the same path with the `/v1` segment removed. The aliasing MUST be applied before routing so the canonical handler runs unchanged.

The aliasing MUST NOT trigger for `/backend-api/codex/v1` or `/backend-api/codex` with no further path. The top-level OpenAI-style `/v1/<rest>` routes are unaffected.

#### Scenario: Misbehaving client requests duplicated prefix
- **WHEN** a client requests `GET /backend-api/codex/v1/models`
- **THEN** the response is identical to `GET /backend-api/codex/models`

#### Scenario: Canonical paths are unchanged
- **WHEN** a client requests `GET /backend-api/codex/models` or `GET /v1/models`
- **THEN** the request is routed to its existing handler without modification
