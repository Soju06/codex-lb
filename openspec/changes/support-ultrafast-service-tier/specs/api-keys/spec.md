## ADDED Requirements

### Requirement: API keys can enforce ultrafast service tier
The dashboard API key CRUD surface MUST allow callers to persist `ultrafast` as an optional enforced service tier. The service MUST preserve `ultrafast` literally when returning API key payloads and when applying API-key enforcement to upstream requests.

#### Scenario: Create API key with ultrafast service tier
- **WHEN** an admin creates an API key with `enforcedServiceTier: "ultrafast"`
- **THEN** the persisted API key stores `ultrafast`
- **AND** the response returns `enforcedServiceTier: "ultrafast"`

#### Scenario: Enforced ultrafast tier overrides request payload
- **WHEN** an API key is configured with `enforcedServiceTier: "ultrafast"`
- **AND** an incoming Responses request asks for `service_tier: "default"`
- **THEN** the forwarded upstream payload uses `service_tier: "ultrafast"`

### Requirement: Cost accounting prices ultrafast service tier
When computing API key `cost_usd` usage, the system MUST price `service_tier: "ultrafast"` with the priority-tier rate schedule while preserving the literal service-tier value for logs and responses.

#### Scenario: Ultrafast-tier request increments cost limit
- **WHEN** an authenticated request for a priced model is finalized with `service_tier: "ultrafast"`
- **THEN** the system computes `cost_usd` using the priority-tier rate for that model
