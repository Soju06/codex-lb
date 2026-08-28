## MODIFIED Requirements

### Requirement: Usage refresh cools down repeated auth-like failures

Background usage refresh MUST apply a cooldown to accounts that repeatedly fail usage refresh with ambiguous `401` or `403` responses. Accounts in that cooldown window MUST be skipped until the cooldown expires or a later successful refresh clears it.

#### Scenario: Zero-capacity monthly primary does not keep free accounts rate-limited
- **GIVEN** a free-plan account whose persisted status is `rate_limited`
- **AND** its latest primary usage row is a zero-capacity non-5h window (for example a monthly upstream snapshot)
- **AND** its normalized quota state reports available monthly quota
- **WHEN** codex-lb derives account status for account summaries or proxy runtime state
- **THEN** the non-5h primary row is ignored for rate-limit recovery
- **AND** the account is treated as `active`
- **AND** downstream account views keep the monthly-only quota presentation
