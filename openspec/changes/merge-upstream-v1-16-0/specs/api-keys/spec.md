## ADDED Requirements

### Requirement: API-key limit reset and filtering merge with enforced tiers

The merged API-key implementation MUST include upstream limit reset window behavior and dashboard request-log API-key filtering while preserving local enforced service-tier behavior.

#### Scenario: API-key enforced service tier still controls proxy reservation

- **GIVEN** an API key has an enforced service tier
- **WHEN** a proxy request authenticated by that key supplies a different tier
- **THEN** request-limit reservation and upstream payload use the enforced tier according to the existing local contract

#### Scenario: API-key request-log filter works with merged repository query

- **WHEN** the dashboard requests logs filtered by API key
- **THEN** the repository applies the API-key filter with the merged indexes and schema
- **AND** pagination and existing filters remain correct
