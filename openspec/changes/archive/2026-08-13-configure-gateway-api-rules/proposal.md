# Configure Gateway API HTTPRoute rules

## Why

The Helm chart's Gateway API support always renders one backend-only catch-all
rule. Operators cannot apply authentication or other Gateway filters to only
selected paths while leaving API endpoints directly accessible without
replacing the chart-managed HTTPRoute.

## What Changes

- Add an optional `gatewayApi.rules` list for per-rule Gateway API `matches`
  and `filters`.
- Keep the codex-lb Service as the backend for every configured rule.
- Preserve the existing catch-all rule when no custom rules are configured.
- Document and test a direct API path plus filtered dashboard catch-all.

## Impact

- **Spec**: `deployment-networking`
- **Helm**: optional Gateway API routing configuration; defaults are unchanged.
- **Runtime/UI**: no application, database, migration, or dashboard changes.
