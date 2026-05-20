## ADDED Requirements

### Requirement: Live snapshot exposes context-window failure diagnostics

The live snapshot helper SHALL expose a bounded `context_length_exceeded`
request-log diagnostic section that helps operators distinguish continuing
context-window failures from generic upstream instability. The section MUST
include aggregate groups and recent request rows using persisted request-log
fields only. Recent rows MUST include requested time, model, transport,
reasoning effort, input tokens, cached input tokens, latency, error code, a
bounded error message preview, and a hashed session identifier when a session id
is available. The diagnostic MUST NOT expose raw prompt text, auth headers, raw
request payloads, or raw session identifiers.

#### Scenario: context-window failures are grouped

- **WHEN** recent request logs contain `context_length_exceeded` rows
- **THEN** the live snapshot output includes a `context_length_exceeded.count`
- **AND** it includes bounded aggregate groups by safe runtime fields such as
  model, transport, reasoning effort, tier fields, status, error code, and input
  token bucket

#### Scenario: recent context-window failures redact session ids

- **WHEN** recent request logs contain `context_length_exceeded` rows with a
  session id
- **THEN** the live snapshot recent rows include `session_id_hash`
- **AND** the raw session id is not present in the snapshot output
- **AND** the recent rows do not include raw prompt text, auth headers, or raw
  request payloads
