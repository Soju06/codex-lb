## ADDED Requirements

### Requirement: Helm Grafana dashboard titles are configurable

The Helm chart MUST allow operators to override the titles of packaged Grafana
dashboards by JSON filename. The default values MUST preserve the packaged
dashboard titles.

#### Scenario: Operator uses concise titles in a folder hierarchy

- **GIVEN** Grafana dashboard provisioning is enabled
- **AND** title overrides map `codex-lb.json` to `Overview` and
  `ttft-breakdown.json` to `TTFT Breakdown`
- **WHEN** the chart renders the Grafana dashboard ConfigMap
- **THEN** each dashboard JSON document contains its configured title
- **AND** dashboard UIDs and all panel definitions remain unchanged

#### Scenario: Default titles remain compatible

- **GIVEN** Grafana dashboard provisioning is enabled
- **AND** the operator does not customize dashboard titles
- **WHEN** the chart renders the Grafana dashboard ConfigMap
- **THEN** the overview title remains `codex-lb`
- **AND** the TTFT title remains `codex-lb TTFT Breakdown`
- **AND** each ConfigMap value remains byte-identical to the chart's raw-file rendering
