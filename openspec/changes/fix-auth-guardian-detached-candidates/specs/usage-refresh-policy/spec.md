## ADDED Requirements

### Requirement: Auth Guardian candidate handoff survives session closure

Auth Guardian MUST preserve stable account identities while its candidate-query session is active and MUST execute selected refresh work after that session closes without reading unloaded or expired state from detached persistence objects. Each selected account MUST still be re-read in the separately owned refresh session before eligibility is confirmed and credentials are refreshed.

#### Scenario: Stale candidate crosses the query-session boundary

- **GIVEN** a stale eligible account is selected during an Auth Guardian pass
- **WHEN** the candidate-query session closes before per-account refresh work begins
- **THEN** Auth Guardian refreshes the selected account without a detached-instance failure
- **AND** the refresh worker re-reads the account in its own session before refreshing it
