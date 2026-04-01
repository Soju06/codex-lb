## ADDED Requirements

### Requirement: API keys can enforce a service tier

The dashboard API key CRUD surface MUST allow callers to persist an optional enforced service tier. The service MUST normalize `fast` to the canonical upstream value `priority` before persistence and before returning the API key payload.

#### Scenario: Create API key with fast service tier alias

- **WHEN** a dashboard client creates an API key with `enforcedServiceTier: "fast"`
- **THEN** the request is accepted
- **AND** the persisted API key stores the canonical value `priority`
- **AND** the response returns `enforcedServiceTier: "priority"`

#### Scenario: Update API key with canonical service tier

- **WHEN** a dashboard client updates an API key with `enforcedServiceTier: "flex"`
- **THEN** the persisted API key stores `flex`
- **AND** subsequent reads return `flex`
