# proxy-runtime-observability Specification

## ADDED Requirements

### Requirement: Greenfield runtime baseline defines metrics and alerting coverage

When the greenfield runtime baseline is documented or enabled, the project MUST provide operator guidance for enabling metrics and alerting that covers readiness, process/container health, request success rate, request latency, upstream failure classes, stream timeout/incomplete errors, database lock pressure, and service-tier mismatches.

#### Scenario: Greenfield baseline metrics are enabled

- **WHEN** an operator follows the greenfield runtime baseline
- **THEN** the guidance enables or points to the metrics endpoint configuration
- **AND** the guidance identifies the scrape port and any required optional dependency or container setting

#### Scenario: Greenfield baseline alerts cover request quality

- **WHEN** an operator installs the recommended alerting bundle or copies the documented rules
- **THEN** alerts exist for elevated non-success request rate
- **AND** p95 latency or equivalent latency histogram degradation
- **AND** upstream timeout or stream_incomplete spikes

#### Scenario: Greenfield baseline alerts cover runtime health

- **WHEN** an operator installs the recommended alerting bundle or copies the documented rules
- **THEN** alerts exist for readiness failure, container restarts or OOM kills, database lock pressure, and bridge ownership or continuity error spikes

#### Scenario: Greenfield baseline exposes service-tier mismatch signal

- **WHEN** a request is made with requested service_tier ultrafast
- **AND** upstream reports actual response.service_tier as default or auto
- **THEN** metrics or documented request-log queries allow operators to count the mismatch without inspecting raw prompt data or secret-bearing logs
