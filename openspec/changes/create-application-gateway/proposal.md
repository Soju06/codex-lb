# Create an application-specific Gateway from the Helm chart

## Why

The Helm chart's Gateway API support only renders an HTTPRoute that attaches
to a pre-existing Gateway through `gatewayApi.parentRefs`. Operators without a
shared cluster Gateway must hand-manage one outside the chart before the
HTTPRoute can bind, which breaks the chart's self-contained install contract.

## What Changes

- Add an optional `gatewayApi.gateway.create` mode that renders a Gateway
  dedicated to the release in the release namespace.
- Require an operator-supplied `gatewayApi.gateway.gatewayClassName` when the
  chart-managed Gateway is enabled.
- Default the chart-managed Gateway to a single HTTP listener on port 80 and
  allow operator-defined listeners (for example HTTPS with certificate refs).
- Attach the chart's HTTPRoute to the chart-managed Gateway automatically,
  ignoring `gatewayApi.parentRefs` in this mode.
- Preserve the existing parentRefs-based attachment when the mode is off.

## Impact

- **Spec**: `deployment-networking`
- **Helm**: optional chart-managed Gateway rendering; defaults are unchanged.
- **Runtime/UI**: no application, database, migration, or dashboard changes.
