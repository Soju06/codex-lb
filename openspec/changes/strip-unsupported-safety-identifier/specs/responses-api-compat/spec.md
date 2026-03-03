## ADDED Requirements

### Requirement: Strip safety_identifier before upstream forwarding
Before forwarding Responses payloads upstream, the service MUST remove `safety_identifier` from normalized payloads for both standard and compact Responses endpoints.

#### Scenario: safety_identifier provided in Responses request
- **WHEN** a client sends a valid Responses request including `safety_identifier`
- **THEN** the service accepts the request and forwards payload without `safety_identifier`

#### Scenario: safety_identifier provided in Chat-mapped request
- **WHEN** a client sends a Chat Completions request including `safety_identifier`
- **THEN** the mapped Responses payload forwarded upstream excludes `safety_identifier`
