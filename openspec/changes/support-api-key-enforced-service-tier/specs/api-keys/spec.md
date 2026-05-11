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

### Requirement: API keys can omit priority service-tier submission

The dashboard API key CRUD surface MUST allow callers to persist an `omitPriorityRequest` boolean. When enabled, downstream requests that resolve to `service_tier: "priority"` or the legacy alias `fast` MUST be submitted upstream without a `service_tier` field. Request logs MUST still preserve the requested priority tier and mark that the tier was omitted.

#### Scenario: Create API key with priority omission

- **WHEN** a dashboard client creates an API key with `omitPriorityRequest: true`
- **THEN** the persisted API key stores `omit_priority_request = true`
- **AND** subsequent reads return `omitPriorityRequest: true`

#### Scenario: Priority omission request log annotation

- **WHEN** a request through an API key with `omitPriorityRequest: true` asks for `service_tier: "priority"`
- **THEN** the forwarded upstream payload omits `service_tier`
- **AND** the request log records `requestedServiceTier: "priority"`
- **AND** the request log records `serviceTierOmitted: true`
