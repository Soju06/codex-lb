## ADDED Requirements

### Requirement: OAuth scope key must be consistent without scope cookies
Dashboard OAuth state management MUST use the same scope-key derivation strategy for start, status, and complete requests when `codex_lb_oauth_scope` cookie is missing.

#### Scenario: Device OAuth completes when client does not persist scope cookie
- **GIVEN** a client starts OAuth device flow without sending `codex_lb_oauth_scope`
- **WHEN** the client calls `/api/oauth/complete` and `/api/oauth/status` without persisting cookies
- **THEN** the flow progresses against the same logical OAuth scope and can transition to `success`
