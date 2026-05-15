## MODIFIED Requirements

### Requirement: Cost accounting uses model and service-tier pricing
When computing API key `cost_usd` usage, the system MUST price requests using the resolved model pricing and the authoritative `service_tier` reported by the upstream response when available, falling back to the forwarded request `service_tier` only when the response omits it. Requests sent with non-standard service tiers MUST use the published pricing for the tier actually used instead of falling back to standard-tier pricing.

#### Scenario: Priority-tier request increments cost limit
- **WHEN** an authenticated request for a priced model is finalized with `service_tier: "priority"`
- **THEN** the system computes `cost_usd` using the priority-tier rate for that model

#### Scenario: Flex-tier request increments cost limit
- **WHEN** an authenticated request for a priced model is finalized with `service_tier: "flex"`
- **THEN** the system computes `cost_usd` using the flex-tier rate for that model

#### Scenario: Standard-tier request keeps standard pricing
- **WHEN** an authenticated request for the same model is finalized without `service_tier`
- **THEN** the system computes `cost_usd` using the standard-tier rate

#### Scenario: Enforced service tier reserves and settles consistently
- **WHEN** an API key enforces `service_tier: "priority"` and the caller submits `/v1/chat/completions` with another service tier
- **AND** the request is routed through ChatGPT-web
- **THEN** the pre-request quota reservation uses the enforced `priority` tier
- **AND** the proxied upstream request uses the enforced `priority` tier
- **AND** final API key usage settlement does not reserve or price the caller-supplied tier

#### Scenario: Enforced service tier still forwards default on Platform
- **WHEN** an API key enforces `service_tier: "priority"` and the caller submits `/v1/responses` with another service tier
- **AND** provider selection routes the request through `openai_platform`
- **THEN** the pre-request quota reservation uses the enforced `priority` tier
- **AND** the proxied upstream Platform request uses `service_tier: "default"`
- **AND** final API key usage settlement falls back to `default` when the upstream response omits a service tier
