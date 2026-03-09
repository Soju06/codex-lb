## ADDED Requirements

### Requirement: Preserve service_tier in Chat Completions mapping
When a Chat Completions request includes `service_tier`, the service MUST preserve that field when mapping the request to the internal Responses payload.

#### Scenario: Chat request includes fast-mode tier
- **WHEN** a client sends a valid Chat Completions request with `service_tier: "priority"`
- **THEN** the mapped Responses payload forwarded upstream includes `service_tier: "priority"`
