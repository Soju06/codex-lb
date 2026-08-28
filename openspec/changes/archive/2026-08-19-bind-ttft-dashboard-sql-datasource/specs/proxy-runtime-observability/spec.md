## MODIFIED Requirements

### Requirement: 24-hour TTFT breakdown queries are available

Operators MUST have an OpenSpec context runbook or dashboard artifact with
24-hour TTFT breakdown queries by user agent group, upstream transport,
model/cache ratio, session gap cohort, prompt size cohort, and prewarm
status/outcome.

The shipped Grafana TTFT dashboard MUST declare a visible, single-select
runtime datasource variable named `DS_SQL` that is restricted to PostgreSQL.
Every SQL panel MUST bind to the selected UID through a typed PostgreSQL
datasource object. The Helm chart MUST preserve the dashboard in its existing
sidecar-discoverable ConfigMap, and chart documentation MUST tell operators to
select the PostgreSQL datasource in Grafana.

#### Scenario: Operator investigates TTFT regression

- **WHEN** an operator needs to inspect the last 24 hours of request-log
  latency
- **THEN** the repository provides SQL that reports p50, p90, p95 TTFT and
  total latency for the requested breakdowns

#### Scenario: Sidecar-provisioned dashboard resolves the selected database

- **GIVEN** the Helm chart renders the Grafana dashboard ConfigMap
- **AND** Grafana has a PostgreSQL datasource available
- **WHEN** the operator selects that datasource through `DS_SQL`
- **THEN** all four TTFT panels resolve to the selected datasource UID
- **AND** no panel reports `Datasource ${DS_SQL} was not found`

#### Scenario: Datasource choice remains explicit and deterministic

- **WHEN** Grafana loads the TTFT dashboard
- **THEN** `DS_SQL` is visible to the operator
- **AND** it permits exactly one PostgreSQL datasource selection
- **AND** it does not offer an all-datasources selection
