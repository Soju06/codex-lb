## ADDED Requirements

### Requirement: Helm startup probe timing is configurable

The Helm chart MUST allow operators to override the timing and failure threshold
fields of the application startup probe. Without overrides, the rendered probe
MUST retain the existing effective behavior. The chart MUST continue to own the
startup HTTP handler, MUST enforce Kubernetes' required startup success
threshold of one, and startup overrides MUST NOT alter readiness or liveness
probes.

#### Scenario: Default startup probe remains compatible

- **GIVEN** the operator does not configure startup probe values
- **WHEN** the chart renders the application StatefulSet
- **THEN** the startup probe uses `/health/startup` on the named `http` port
- **AND** its initial delay is 5 seconds
- **AND** its period is 2 seconds
- **AND** its timeout is 1 second
- **AND** its failure threshold is 30
- **AND** its success threshold is 1

#### Scenario: Operator extends the startup budget

- **GIVEN** the operator sets `startupProbe.failureThreshold` to `90`
- **WHEN** the chart renders the application StatefulSet
- **THEN** the startup probe failure threshold is 90
- **AND** all other startup probe fields retain their defaults
- **AND** readiness and liveness probes remain unchanged

#### Scenario: Operator tunes an individual startup probe field

- **GIVEN** the operator overrides one supported startup probe timing or threshold field
- **WHEN** the chart renders the application StatefulSet
- **THEN** only that startup probe field differs from its default
- **AND** the startup HTTP handler remains unchanged

#### Scenario: Operator configures an invalid startup success threshold

- **GIVEN** the operator sets `startupProbe.successThreshold` above one
- **WHEN** Helm validates the values
- **THEN** rendering fails before resources are submitted to Kubernetes
