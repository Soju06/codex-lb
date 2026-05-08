## MODIFIED Requirements

### Requirement: Runtime resolves API key peer fallback URLs

Peer fallback runtime MUST use only peer fallback base URLs configured on the authenticated API key. Runtime fallback MUST NOT use registered targets, disabled targets, or environment-configured peer URLs as a global default.

When the process-level `CODEX_API_KEY` environment variable is configured, peer fallback outbound requests MUST use `Authorization: Bearer <CODEX_API_KEY>` when connecting to the selected peer. When `CODEX_API_KEY` is not configured, the runtime MUST preserve the original forwarded authorization behavior.

#### Scenario: API key peer fallback URLs are used

- **GIVEN** an authenticated API key has peer fallback base URLs
- **WHEN** an eligible pre-output proxy failure triggers peer fallback
- **THEN** the runtime attempts only the peer fallback base URLs configured on that API key

#### Scenario: Peer fallback uses CODEX_API_KEY for peer authentication

- **GIVEN** an authenticated API key has peer fallback base URLs
- **AND** `CODEX_API_KEY` is configured
- **WHEN** an eligible pre-output proxy failure triggers peer fallback
- **THEN** the outbound peer request uses `Authorization: Bearer <CODEX_API_KEY>`
- **AND** the caller's local API key is not forwarded to the peer

#### Scenario: API key without peer fallback URLs disables fallback

- **GIVEN** an authenticated API key has no peer fallback base URLs
- **WHEN** an eligible pre-output proxy failure triggers peer fallback
- **THEN** the runtime does not attempt peer fallback

#### Scenario: Unauthenticated request does not fallback

- **GIVEN** a proxy request has no authenticated API key
- **WHEN** an eligible pre-output proxy failure occurs
- **THEN** the runtime does not attempt peer fallback

#### Scenario: Global targets are not used by default

- **GIVEN** peer fallback targets are registered in the dashboard
- **AND** the authenticated API key does not define peer fallback base URLs
- **WHEN** an eligible pre-output proxy failure triggers peer fallback
- **THEN** the runtime does not attempt those registered targets
