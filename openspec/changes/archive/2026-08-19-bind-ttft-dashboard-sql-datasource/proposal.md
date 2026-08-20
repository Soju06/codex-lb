## Why

The shipped TTFT breakdown dashboard references `${DS_SQL}` on every panel,
but it does not declare that runtime variable. Grafana therefore treats the
literal placeholder as a datasource UID and renders
`Datasource ${DS_SQL} was not found` instead of executing the PostgreSQL
queries.

## What Changes

- Declare `DS_SQL` as a visible, single-select runtime datasource variable
  restricted to the PostgreSQL datasource plugin.
- Bind all four TTFT panels through typed datasource objects whose UID is the
  selected `DS_SQL` value.
- Preserve Helm sidecar ConfigMap packaging and title overrides.
- Document that operators select the PostgreSQL datasource after the sidecar
  provisions the dashboard.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: The shipped TTFT dashboard MUST resolve its
  SQL panels through an operator-selected PostgreSQL datasource.

## Impact

- `deploy/helm/codex-lb/dashboards/ttft-breakdown.json`
- `deploy/helm/codex-lb/README.md`
- focused dashboard-artifact and rendered-ConfigMap tests

There is no application runtime, API, database schema, chart value, sidecar
label, or navigation change.
