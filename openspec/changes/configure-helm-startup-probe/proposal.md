# Configure the Helm startup probe

## Why

The Helm chart hard-codes its startup probe timing. Operators with slower cold
starts cannot increase the startup budget without maintaining a chart fork.

## What Changes

- Add optional values for the existing startup probe timing and failure threshold.
- Keep the Kubernetes-required startup success threshold fixed at one.
- Preserve the current startup probe behavior by default.
- Keep the startup endpoint, readiness probe, and liveness probe unchanged.

## Impact

- **Spec**: `deployment-installation`
- **Helm**: optional startup probe tuning; defaults are unchanged.
- **Runtime**: no application behavior changes.
