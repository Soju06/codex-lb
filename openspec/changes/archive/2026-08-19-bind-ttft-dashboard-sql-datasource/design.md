## Context

The Helm chart packages `ttft-breakdown.json` unchanged in a
sidecar-discoverable ConfigMap. The dashboard's four SQL panels currently use
the legacy scalar `"${DS_SQL}"` datasource form while `templating.list` is
empty. Grafana 12.4.4 therefore has no runtime value to interpolate and can
resolve the placeholder as a missing UID.

Datasource UIDs and credentials are installation-specific. The chart must
remain portable and must not take ownership of provisioning an operator's
Grafana PostgreSQL datasource.

## Goals / Non-Goals

**Goals:**

- Make the selected PostgreSQL datasource explicit, visible, and deterministic
  at dashboard runtime.
- Route all four panels through the same selected UID using Grafana 12.4.4's
  typed datasource-reference schema.
- Preserve current SQL, layout, sidecar packaging, title overrides, and chart
  values.

**Non-Goals:**

- Provisioning a Grafana datasource, PostgreSQL credentials, or database
  permissions.
- Adding a Helm value for a cluster-specific datasource UID.
- Changing TTFT queries, visualizations, panel layout, navigation, or
  application runtime behavior.

## Decisions

### Use a classic datasource template variable

Declare `DS_SQL` with `type: datasource` and plugin query
`grafana-postgresql-datasource`. Keep it visible, single-select, and without
an all-datasources option.

Alternative: hard-code a datasource UID in Helm. Rejected because UIDs are
installation-specific and would require another chart setting for a Grafana
runtime concern.

### Use typed panel datasource references

Each panel uses:

```json
{
  "type": "grafana-postgresql-datasource",
  "uid": "${DS_SQL}"
}
```

Grafana 12.4.4 defines panel datasources as `{type, uid}` references and
interpolates variables in the UID field. The existing scalar form is legacy
input and does not satisfy the current schema.

Alternative: leave panel references scalar and only add the variable.
Rejected because it preserves the ambiguous representation that produced the
missing-datasource state.

### Preserve the existing Helm packaging seam

Keep dashboard JSON under `dashboards/` and let
`templates/grafana-dashboard.yaml` package it unchanged. A rendered ConfigMap
test proves the runtime variable and typed panel references survive Helm.

Alternative: generate the variable in the template. Rejected because it
duplicates dashboard structure in Go templates and makes standalone dashboard
validation harder.

## Risks / Trade-offs

- **No PostgreSQL datasource exists** → The dropdown has no valid selection;
  chart documentation states that operators must provision and select one.
- **A saved selection becomes stale** → The variable remains visible so the
  operator can select another ordinary PostgreSQL datasource.
- **A datasource connects but lacks request-log access** → Grafana reports the
  database/query error normally; this change only owns datasource resolution.
- **Grafana schema behavior changes** → Focused artifact tests and a real
  Grafana 12.4.4 API/browser scenario lock the supported contract.

## Migration Plan

1. Upgrade or redeploy the chart with Grafana dashboard sidecar support
   enabled.
2. Let the sidecar replace the dashboard ConfigMap payload.
3. Open the TTFT dashboard and select the PostgreSQL datasource that points to
   the codex-lb database.

Rollback is a chart rollback to the previous dashboard JSON. No database,
application, secret, or chart-value migration is involved.

## Open Questions

None. Grafana 12.4.4 documentation and source confirm the plugin ID,
datasource-variable flags, typed panel reference, and UID interpolation path.
