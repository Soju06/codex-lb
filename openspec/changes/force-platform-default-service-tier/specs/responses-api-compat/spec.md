## MODIFIED Requirements

### Requirement: Preserve supported service_tier values
When a Responses request includes `service_tier`, the service MUST preserve that field in the normalized ChatGPT-web upstream payload instead of dropping or rewriting it locally. When the same request is routed through `openai_platform`, the service MUST forward `service_tier: "default"` upstream regardless of the requested or enforced service tier.

#### Scenario: Responses request includes fast-mode tier
- **WHEN** a client sends a valid Responses request with `service_tier: "priority"`
- **AND** the request is routed through ChatGPT-web
- **THEN** the service accepts the request and forwards `service_tier: "priority"` upstream unchanged

#### Scenario: Platform Responses request uses default tier
- **WHEN** a client sends a valid Responses request with `service_tier: "priority"`
- **AND** provider selection routes the request through `openai_platform`
- **THEN** the upstream Platform payload includes `service_tier: "default"`
- **AND** request logs keep `requested_service_tier: "priority"`

#### Scenario: Platform compact request uses default tier
- **WHEN** a client sends a compact Responses request with `service_tier: "priority"`
- **AND** provider selection routes the request through `openai_platform`
- **THEN** the upstream Platform compact payload includes `service_tier: "default"`
- **AND** request logs keep `requested_service_tier: "priority"`
