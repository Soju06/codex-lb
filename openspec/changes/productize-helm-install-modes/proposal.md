## Why

The Helm chart should be easy to install across Docker Desktop, kind, EKS, GKE, OKE, and other Kubernetes environments without requiring users to rediscover migration timing, database wiring, or vendor-specific setup contracts. The important dimension is install mode, not cloud provider.

## What Changes

- Add an application schema gate initContainer so pods do not start their main container until Alembic head is visible when startup migrations are disabled.
- Keep install behavior mode-centric across bundled PostgreSQL, direct external database, and External Secrets modes.
- Publish mode-specific values overlays and rewrite Helm chart documentation around those contracts.
- Add a kind-based Helm smoke install workflow for bundled and external DB modes.

## Capabilities

### Modified Capabilities

- `database-migrations`
- `database-backends`

### New Capabilities

- `deployment-installation`
