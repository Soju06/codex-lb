## ADDED Requirements

### Requirement: Company catalogs reflect observed usability
The service SHALL run a minimal inference health check for every enabled company model every two hours. Each check SHALL have a 30-second deadline and SHALL record its model, outcome, latency, usage and completion time separately from user traffic. Only the elected scheduler leader SHALL issue probes. Client model catalogs SHALL advertise a company model only when its latest scheduled check succeeded within the freshness window and completed within 30 seconds. User-request outcomes SHALL NOT determine catalog visibility. Source credential unavailability or exhausted local budget SHALL hide all models belonging to that source. Configured model settings and direct explicit requests SHALL remain available. Catalog reads SHALL NOT perform inference probes.

#### Scenario: Model recovery
- **WHEN** a hidden company model has a later qualifying scheduled health check
- **THEN** the next catalog fetch includes that model subject to source admission state

#### Scenario: Source and model isolation
- **WHEN** one model fails upstream and its sibling has a qualifying success
- **THEN** only the failed model is hidden unless shared source admission blocks both

#### Scenario: Unknown and stale models
- **WHEN** a company model has no fresh successful scheduled health check
- **THEN** both client catalog formats omit that model without deleting its configuration

#### Scenario: User traffic does not change visibility
- **WHEN** a user request succeeds, fails, times out, or contains unsupported input
- **THEN** the catalog continues to use the latest scheduled health-check result
