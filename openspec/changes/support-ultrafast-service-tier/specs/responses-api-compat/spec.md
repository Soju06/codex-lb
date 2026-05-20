## ADDED Requirements

### Requirement: Preserve ultrafast service tier literally
When a Responses or compact Responses request includes `service_tier: "ultrafast"`, the service MUST preserve that literal value in the normalized upstream payload and local requested-tier state. The service MUST NOT canonicalize `ultrafast` to `priority` or treat it as a `fast` alias.

#### Scenario: Responses request includes ultrafast tier
- **WHEN** a client sends a valid Responses request with `service_tier: "ultrafast"`
- **THEN** the service accepts the request
- **AND** the outbound upstream payload includes `service_tier: "ultrafast"`
- **AND** request-log requested/effective tier fallback state uses `ultrafast` unless the upstream reports a different actual tier

#### Scenario: Compact request includes ultrafast tier
- **WHEN** a client sends a valid compact Responses request with `service_tier: "ultrafast"`
- **AND** the upstream response omits an actual `service_tier`
- **THEN** the request is priced and logged using the literal requested tier `ultrafast`
