## MODIFIED Requirements

### Requirement: Preserve supported service_tier values
When a Responses request includes a non-default `service_tier`, the service MUST preserve that field in the normalized upstream payload instead of dropping or rewriting it locally, except for documented aliases. The service MUST canonicalize `service_tier: "fast"` to `service_tier: "priority"` for upstream compatibility, MUST preserve `service_tier: "ultrafast"` literally, and MUST treat request-side `service_tier: "default"` as equivalent to omitting the field before forwarding upstream.

#### Scenario: Responses request includes fast-mode tier
- **WHEN** a client sends a valid Responses request with `service_tier: "priority"`
- **THEN** the service accepts the request and forwards `service_tier: "priority"` upstream unchanged

#### Scenario: Responses request includes default tier
- **WHEN** a client sends a valid Responses request with `service_tier: "default"`
- **THEN** the service accepts the request
- **AND** the outbound upstream payload omits `service_tier`
- **AND** response/request-log parsing can still record `service_tier: "default"` when upstream reports it
