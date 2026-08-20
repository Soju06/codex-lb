## ADDED Requirements

### Requirement: API keys can enforce the Ultrafast service tier

The dashboard API key CRUD surface MUST accept and persist `ultrafast` as a canonical enforced service tier. The service MUST return the same canonical value and MUST NOT normalize it to `priority`.

#### Scenario: Create an API key with Ultrafast enforcement

- **WHEN** a dashboard client creates an API key with `enforcedServiceTier: "ultrafast"`
- **THEN** the request is accepted
- **AND** the persisted and returned enforced service tier is `ultrafast`

#### Scenario: Enforce Ultrafast on an advertising model

- **GIVEN** an account model advertises the `ultrafast` service tier
- **WHEN** a request uses an API key whose enforced service tier is `ultrafast`
- **THEN** the upstream request carries `service_tier: "ultrafast"`
