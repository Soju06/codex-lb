# Configure Grafana dashboard titles

## Why

The Helm chart exposes a Grafana folder annotation but hard-codes the titles of
its provisioned dashboards. Operators cannot use concise titles inside an
installation-specific folder hierarchy without maintaining copies of the
dashboard JSON.

## What Changes

- Add dashboard title overrides keyed by the packaged JSON filename.
- Preserve the existing titles by default.
- Apply configured titles while rendering the dashboard ConfigMap.

## Impact

- **Spec**: `deployment-installation`
- **Helm**: optional Grafana dashboard title configuration; defaults are unchanged.
- **Runtime/UI**: no application or built-in dashboard changes.
