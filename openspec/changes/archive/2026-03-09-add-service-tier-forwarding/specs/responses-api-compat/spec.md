## ADDED Requirements

### Requirement: Preserve supported service_tier values
When a Responses request includes `service_tier`, the service MUST preserve that field in the normalized upstream payload instead of dropping or rewriting it locally.

#### Scenario: Responses request includes fast-mode tier
- **WHEN** a client sends a valid Responses request with `service_tier: "priority"`
- **THEN** the service accepts the request and forwards `service_tier: "priority"` upstream unchanged
