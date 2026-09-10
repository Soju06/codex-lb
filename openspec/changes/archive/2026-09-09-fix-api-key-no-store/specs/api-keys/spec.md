## ADDED Requirements

### Requirement: One-time API-key secret responses prevent storage

Every successful response containing a full plain API key MUST include
`Cache-Control: no-store, no-cache, must-revalidate, private`,
`Pragma: no-cache`, and `Expires: 0`. This applies to both create URL forms and
regeneration. The policy MUST NOT alter payload, generation, persistence,
authorization, errors, or logging; plain keys MUST remain absent from logs.

#### Scenario: Create through either collection URL

- **WHEN** an authorized admin creates a key through either URL form
- **THEN** all three directives are present
- **AND** the existing one-time plain-key payload remains

#### Scenario: Regenerate a key

- **WHEN** an authorized admin regenerates a key
- **THEN** all three directives are present
- **AND** the existing regenerated-key payload remains

#### Scenario: Unauthorized write stays rejected

- **WHEN** a read-only principal attempts create or regenerate
- **THEN** existing 403 behavior remains
- **AND** no plain key or secret-response headers are returned
