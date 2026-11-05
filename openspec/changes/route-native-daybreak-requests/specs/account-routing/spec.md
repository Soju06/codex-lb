## ADDED Requirements

### Requirement: Automatic Daybreak scope is an intersection
Automatic Daybreak routing MUST pass the existing security-work-authorized requirement to account selection before the first attempt and every retry. Eligible accounts MUST remain within the API key's permitted scope and satisfy the existing account-level Trusted Cyber toggle, ownership, availability and model constraints. An empty eligible pool MUST fail without ordinary-account fallback.

#### Scenario: Authorized account is unavailable or outside the key scope
- **WHEN** no permitted account satisfies Trusted Cyber eligibility and existing routing constraints
- **THEN** the request fails before ordinary-account dispatch
