## ADDED Requirements

### Requirement: Per-model scheduled health visibility
The authenticated model-source list SHALL expose each company model's latest completed scheduled probe, with healthy, unhealthy, slow, stale and unknown states. It SHALL expose the completion and expiry times, latency, error code and known input/output token usage; missing measurements SHALL remain null. The dashboard's catalog-visible flag SHALL use the same eligibility rule as client catalogs, including source/model enablement, credentials and local budget. User traffic SHALL NOT replace scheduled probe evidence. Non-company sources SHALL retain their existing presentation.

#### Scenario: Failed model alongside a healthy sibling
- **WHEN** one model's latest scheduled check failed and a sibling's check passed
- **THEN** both configured models remain in the dashboard with independent health and catalog visibility
- **AND** the failure code, completion time and measured latency are visible

#### Scenario: Unknown, expired and slow results
- **WHEN** a model has no probe, an expired probe, or a successful probe exceeding the latency threshold
- **THEN** the dashboard distinguishes unknown, stale and slow respectively and marks the model hidden
- **AND** absent token usage is not displayed as zero

### Requirement: Passive health dashboard and admission separation
Dashboard reads and refreshes SHALL NOT initiate model inference. The view SHALL label the two-hour probe interval, 30-second deadline and three-hour freshness window and SHALL show completed fresh checks among enabled models, not claim a currently running check without evidence. Source cooldown and replica-local in-flight count/concurrency limit SHALL be displayed separately from probe health and SHALL NOT falsely change probe-based catalog visibility. The UI SHALL explain that a minimal probe does not validate large contexts or cross-provider compressed history.

#### Scenario: Busy source with a successful probe
- **WHEN** a source is at its replica-local concurrency limit but has a fresh successful probe
- **THEN** the dashboard shows the concurrency limitation separately while preserving the probe state and catalog eligibility
- **AND** refreshing the dashboard consumes no inference tokens
