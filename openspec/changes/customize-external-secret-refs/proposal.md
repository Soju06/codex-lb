# Change: customize-external-secret-refs

## Why

The Helm chart assumes the database URL and encryption key are JSON properties
in one remote secret named after the release. Secret stores such as Infisical
commonly model them as separate secrets at provider-specific paths, forcing
operators to maintain a duplicate ExternalSecret outside the chart. The chart
also renders the retired `external-secrets.io/v1beta1` API, which is not served
by current External Secrets Operator installations.

## What Changes

- Render chart-managed ExternalSecret resources with `external-secrets.io/v1`.
- Allow the database URL and encryption key to configure independent remote
  keys and optional JSON properties.
- Preserve the existing release-name and JSON-property behavior by default.
- Document and test an Infisical-style individual-secret layout.

## Impact

- Affected spec: `deployment-installation`
- Affected code: Helm values, schema, template, chart documentation, and chart
  rendering tests
- Existing values remain compatible; the generated ExternalSecret API advances
  from the retired beta version to the stable version. Clusters must run
  External Secrets Operator v0.17.0 or newer — the first release that serves
  `external-secrets.io/v1`; older operators cannot apply the rendered
  resource, and the chart README documents this floor.
