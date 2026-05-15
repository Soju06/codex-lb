## MODIFIED Requirements

### Requirement: Greenfield baseline preserves operator tier preference

Operator guidance for tiered traffic in the greenfield baseline MUST preserve
the operator's selected Codex CLI service tier. The guidance MUST NOT recommend
changing an existing ultrafast preference solely because the runtime is busy.
The guidance MUST instead require verification of requested tier versus actual
upstream served tier. The live snapshot verification path MUST expose safe
runtime-correlation groups and recent request fields that include model,
transport, status, requested service tier, actual service tier, token buckets,
and latency fields without including prompt text or raw request payloads.

#### Scenario: Greenfield baseline verifies ultrafast

- **WHEN** an operator uses Codex CLI with service_tier set to ultrafast
- **THEN** the greenfield baseline keeps that requested tier unchanged
- **AND** provides a verification path that compares requested_service_tier to
  actual_service_tier
- **AND** the verification path includes runtime-correlation fields for model,
  transport, status, token buckets, and latency
- **AND** it does not include prompt text or raw request payloads
