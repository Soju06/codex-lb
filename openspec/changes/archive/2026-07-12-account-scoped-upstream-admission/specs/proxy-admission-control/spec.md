## MODIFIED Requirements

### Requirement: Local overload reasons are stable and distinguishable

Local Responses overload failures MUST expose stable low-cardinality reason fields in logs and metrics so operators can distinguish `bridge_queue_full`, `response_create_gate_timeout`, `hard_affinity_saturated`, `previous_response_owner_unavailable`, `global_admission_timeout`, `capacity_exhausted_active_sessions`, `account_response_create_cap`, and `account_stream_cap`. These local reasons MUST NOT be reported as upstream rate limits. Local usage snapshots and synthetic budget pressure MUST NOT be converted into local overload unless an explicit operator-configured local admission policy is exhausted.

#### Scenario: Bridge queue saturation is not ambiguous

- **WHEN** a local HTTP bridge queue rejects a request
- **THEN** logs and metrics use the stable reason `bridge_queue_full`
- **AND** they do not use the ambiguous alias `queue_full`

#### Scenario: Account cap rejection is local overload

- **WHEN** every eligible account is unavailable because of account-local caps
- **THEN** the HTTP response is a local overload response with `Retry-After`
- **AND** logs and metrics identify `account_response_create_cap` or `account_stream_cap`

#### Scenario: Usage snapshot exhaustion is not local overload

- **GIVEN** at least one account is active and below explicit local concurrency caps
- **AND** local usage snapshots report exhausted standard budget
- **WHEN** foreground proxy routing evaluates the request
- **THEN** the request is not rejected as local overload solely because of those snapshots
