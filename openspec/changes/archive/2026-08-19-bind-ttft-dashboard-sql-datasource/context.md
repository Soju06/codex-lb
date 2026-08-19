# Context: TTFT dashboard SQL datasource binding

## Purpose

Make the sidecar-provisioned TTFT dashboard immediately bindable to an
operator's PostgreSQL datasource without baking a cluster-specific UID into
the chart.

## Decisions

- `DS_SQL` remains a runtime dashboard variable because datasource UIDs differ
  across Grafana installations.
- The variable is visible and single-select so operators can inspect and
  change the active PostgreSQL datasource without editing dashboard JSON.
- Every panel uses Grafana's typed datasource object with PostgreSQL plugin
  type and `${DS_SQL}` UID. One dashboard variable remains the single source
  of truth for all four panels.
- The dashboard stays in `dashboards/*.json`; the existing Helm template,
  sidecar labels, folder annotation, and optional title override remain
  unchanged.

## Constraints

- Do not add a Helm value for a datasource UID: datasource selection is a
  Grafana runtime concern and a new chart setting would duplicate the
  dashboard variable.
- Do not provision PostgreSQL credentials or a Grafana datasource from this
  chart.
- Preserve each panel's SQL, layout, IDs, titles, and visualization type.

## Failure Modes

- A scalar `"datasource": "${DS_SQL}"` is ambiguous to modern Grafana and can
  be resolved as a literal missing UID.
- A hidden, multi-value, or include-all variable can make panel execution
  non-deterministic or leave operators unable to repair a stale selection.
- A variable that accepts non-PostgreSQL plugins can select a datasource that
  cannot execute the dashboard's SQL.

## Example

After the Grafana sidecar imports `ttft-breakdown.json`, an operator opens the
dashboard and selects datasource UID `codex-lb-postgres` from the `DS_SQL`
dropdown. All four panels resolve to:

```json
{
  "type": "grafana-postgresql-datasource",
  "uid": "${DS_SQL}"
}
```

Grafana substitutes `codex-lb-postgres` at runtime and executes every TTFT
query against that datasource.
