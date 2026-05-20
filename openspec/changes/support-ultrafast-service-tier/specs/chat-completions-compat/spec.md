## ADDED Requirements

### Requirement: Preserve ultrafast service_tier in Chat Completions mapping
When a Chat Completions request includes `service_tier: "ultrafast"`, the service MUST preserve that literal field when mapping the request to the internal Responses payload.

#### Scenario: Chat request includes ultrafast tier
- **WHEN** a client sends a valid Chat Completions request with `service_tier: "ultrafast"`
- **THEN** the mapped Responses payload forwarded upstream includes `service_tier: "ultrafast"`
