## ADDED Requirements

### Requirement: Warmup decisions are claimed before synthetic traffic

Warmup execution SHALL atomically transition a planned decision to `executing`
before reserving API-key budget or sending synthetic probe traffic. Final
outcomes such as `executed`, `failed`, or API-key skip reasons MUST only update
decisions that are still `executing`. Cancellation MUST only update decisions
that are still queued or skipped and MUST NOT cancel an in-flight `executing`
decision.

#### Scenario: Planned warmup is claimed before probe send

- **GIVEN** a planned warmup decision is eligible to run
- **WHEN** warm-now starts sending the synthetic probe
- **THEN** the persisted decision status is already `executing`
- **AND** a concurrent worker cannot claim the same planned decision

#### Scenario: Executing warmup cannot be canceled

- **GIVEN** a warmup decision is already `executing`
- **WHEN** an operator requests cancellation
- **THEN** the decision remains `executing`
- **AND** the response reports that the decision is not cancelable
