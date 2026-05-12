## ADDED Requirements

### Requirement: Dashboard session lifetime merge preserves auth boundaries

The upstream dashboard session lifetime setting MUST be merged without weakening existing dashboard authentication, TOTP, password, bootstrap-token, or API-key auth boundaries.

#### Scenario: Existing session expiry is preserved through TOTP

- **GIVEN** a dashboard session has an existing expiry
- **WHEN** the user completes TOTP or related dashboard auth flows
- **THEN** the merged service preserves the expected expiry semantics
- **AND** it does not silently extend or remove authentication requirements

#### Scenario: Invalid session lifetime input is rejected

- **WHEN** an administrator submits an invalid dashboard session lifetime value
- **THEN** the backend rejects it with a validation error
- **AND** the frontend surfaces the rejected state without corrupting other settings
