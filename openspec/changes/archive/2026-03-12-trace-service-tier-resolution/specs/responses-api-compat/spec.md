## ADDED Requirements

### Requirement: Emit opt-in safe service-tier trace logs
When service-tier trace logging is enabled, the service MUST emit a diagnostic log entry for Responses requests that records `request_id`, request `kind`, `requested_service_tier`, and upstream `actual_service_tier`. The diagnostic log MUST NOT include prompt text, input content, or the full request payload.

#### Scenario: Streaming request logs requested and actual service tiers
- **WHEN** a streaming Responses request is sent with `service_tier: "priority"` and the upstream stream reports `response.service_tier: "default"`
- **THEN** the service emits a diagnostic log entry containing `requested_service_tier=priority` and `actual_service_tier=default`

#### Scenario: Compact request keeps actual tier empty when upstream omits it
- **WHEN** a compact Responses request is sent with `service_tier: "priority"` and the upstream JSON response omits `service_tier`
- **THEN** the service emits a diagnostic log entry containing `requested_service_tier=priority` and `actual_service_tier=None`
