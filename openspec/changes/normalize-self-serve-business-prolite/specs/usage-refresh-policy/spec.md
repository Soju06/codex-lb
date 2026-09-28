## ADDED Requirements

### Requirement: Usage refresh canonicalizes known upstream plan aliases

Codex-lb MUST canonicalize the upstream account plan identifier
`self_serve_business_prolite` to its existing `prolite` plan before evaluating
workspace-less plan transitions, persisting account metadata, resolving usage
capacity, parsing rate-limit plan metadata, or checking model-plan eligibility.
The raw upstream alias MUST NOT be stored as a separate local account tier.
Plan identifiers without a known canonical alias MUST retain the existing
unknown-plan behavior.

#### Scenario: Business Premium alias refresh is accepted

- **GIVEN** an active workspace-less account with stored `plan_type` `team`
- **WHEN** usage refresh returns `plan_type` `self_serve_business_prolite`
- **THEN** the usage sample is written and the stored `plan_type` becomes `prolite`

#### Scenario: Business Premium alias uses Prolite entitlements

- **GIVEN** an upstream plan identifier `self_serve_business_prolite`
- **WHEN** codex-lb resolves capacity and model-plan eligibility
- **THEN** it uses the same capacity and Pro-equivalent eligibility as `prolite`

#### Scenario: Unknown future plan remains unknown

- **GIVEN** a plan identifier with no recognized plan or alias
- **WHEN** codex-lb canonicalizes the identifier
- **THEN** it preserves the cleaned identifier without assigning a known tier
