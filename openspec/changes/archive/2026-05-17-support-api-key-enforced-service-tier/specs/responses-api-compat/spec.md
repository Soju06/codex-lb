## ADDED Requirements

### Requirement: API key service tier enforcement applies to upstream Responses requests

When an API key carries an enforced service tier, the proxy MUST override any incoming Responses request service tier with that enforced value before forwarding upstream. The legacy alias `fast` MUST be treated as `priority`.

#### Scenario: Enforced service tier overrides the request payload

- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request asks for `service_tier: "default"`
- **THEN** the forwarded upstream payload uses `service_tier: "priority"`

#### Scenario: Fast alias is applied as priority

- **WHEN** an API key is configured with `enforcedServiceTier: "fast"`
- **THEN** the forwarded upstream payload uses the canonical value `priority`
