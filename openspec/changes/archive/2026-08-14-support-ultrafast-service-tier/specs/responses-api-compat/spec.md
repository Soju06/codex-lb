## ADDED Requirements

### Requirement: Responses routes preserve the Ultrafast service tier

Responses-compatible routes MUST accept the canonical `ultrafast` service tier and MUST forward it unchanged. When upstream reports the actual response tier, request logging MUST preserve `ultrafast` using the existing requested, actual, and billable tier contract.

#### Scenario: Explicit Ultrafast request is forwarded

- **WHEN** a client sends a Responses request with `service_tier: "ultrafast"`
- **THEN** the forwarded upstream payload contains `service_tier: "ultrafast"`

#### Scenario: Upstream confirms Ultrafast processing

- **WHEN** upstream completes a request with `response.service_tier: "ultrafast"`
- **THEN** the actual and billable request-log tiers are `ultrafast`
