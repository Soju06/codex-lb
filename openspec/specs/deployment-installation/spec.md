# deployment-installation Specification

## Purpose

Define installation modes and smoke-test expectations so the Helm chart remains portable across supported deployments.

## Requirements

### Requirement: Helm chart is organized around install modes

The Helm chart MUST document and support three primary install modes: bundled PostgreSQL, direct external database, and external secrets. These install contracts MUST be portable across Kubernetes providers without requiring provider-specific chart forks.

#### Scenario: Bundled mode values exist

- **WHEN** a user wants a self-contained install
- **THEN** the chart provides a bundled mode values overlay with bundled PostgreSQL enabled

#### Scenario: External DB mode values exist

- **WHEN** a user wants to install against an already reachable PostgreSQL database
- **THEN** the chart provides an external DB values overlay and accepts direct DB URL or DB secret wiring

#### Scenario: External secrets mode values exist

- **WHEN** a user wants to source credentials from External Secrets Operator
- **THEN** the chart provides an external secrets values overlay that keeps migration and startup behavior fail-closed

#### Scenario: External secrets mode requires a SecretStore reference

- **WHEN** external secrets mode is enabled without `externalSecrets.secretStoreRef.name`
- **THEN** Helm rendering fails with an explicit configuration error

### Requirement: Helm chart checks are automated

The project MUST run automated OpenSpec and Helm render checks in CI for the reference chart. The Helm checks MUST cover the production External Secrets overlay failure path and a successful render path with an explicit placeholder SecretStore reference.

#### Scenario: Production External Secrets overlay fails without store name

- **WHEN** CI renders `values-prod.yaml` without `externalSecrets.secretStoreRef.name`
- **THEN** Helm rendering fails with an explicit configuration error

#### Scenario: Production External Secrets overlay renders with store name

- **WHEN** CI renders `values-prod.yaml` with `externalSecrets.secretStoreRef.name`
- **THEN** Helm rendering succeeds
- **AND** the ExternalSecret contains the configured SecretStore reference

### Requirement: Helm support policy is pinned to modern Kubernetes minors

The chart MUST declare a minimum supported Kubernetes version of `1.32`, and CI MUST validate chart rendering against a `1.35` baseline instead of older legacy minors.

#### Scenario: Chart metadata declares the minimum supported version

- **WHEN** a user inspects the chart metadata and README
- **THEN** the documented minimum supported Kubernetes version is `1.32`

#### Scenario: CI validates the modern baseline

- **WHEN** CI runs Helm render validation
- **THEN** the validation set includes Kubernetes `1.35`
- **AND** pre-`1.32` validation targets are not treated as the support baseline
