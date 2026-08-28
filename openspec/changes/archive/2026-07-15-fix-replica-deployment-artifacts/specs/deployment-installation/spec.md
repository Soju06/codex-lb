# deployment-installation Delta

## MODIFIED Requirements

### Requirement: Helm install modes are smoke-tested

The project MUST run automated Helm smoke installs for the easy-setup install modes in CI. CI Helm smoke installs MUST avoid avoidable external image pulls for chart test pods when the application image has already been built and loaded into the disposable cluster. Smoke scripts MUST emit timestamped logs for major phases so CI output identifies where time is spent. Smoke scripts MUST bound Helm test waits with a configurable timeout.

#### Scenario: Bundled and external DB modes are smoke tested

- **WHEN** CI runs Helm smoke installation checks
- **THEN** it installs the chart on a disposable Kubernetes cluster in bundled mode
- **AND** it installs the chart on a disposable Kubernetes cluster in external DB mode
- **AND** both installs reach a healthy testable state

#### Scenario: CI Helm test uses the loaded application image

- **WHEN** CI runs kind-based Helm smoke checks after loading the application image into the cluster
- **THEN** the Helm test pod image is overridden to the loaded application image
- **AND** the chart default test pod image remains equivalent to `docker.io/library/busybox:1.37` for normal installs

#### Scenario: External DB smoke exercises the default two-replica topology

- **WHEN** CI runs the external DB smoke installation
- **THEN** the application release is installed with two replicas
- **AND** both application pods become Ready
- **AND** `/health/ready` served by an application pod reports a bridge ring of size 2 with the probed pod an active member
- **AND** the smoke fails when the bridge ring probe emits no confirmation output, so a probe that silently no-ops cannot pass
- **AND** the smoke still validates external database mode by using an external PostgreSQL release

#### Scenario: Bundled smoke remains single-replica

- **WHEN** CI runs the bundled smoke installation
- **THEN** the application release is installed with one replica to bound disposable-cluster resource cost

#### Scenario: Helm smoke phases are timestamped

- **WHEN** CI runs kind-based Helm smoke checks
- **THEN** major phases emit UTC timestamped log lines

#### Scenario: Helm test wait is bounded

- **WHEN** CI runs kind-based Helm smoke checks
- **THEN** each `helm test` invocation uses the configured Helm test timeout
- **AND** the default timeout is shorter than Helm's default wait window

## ADDED Requirements

### Requirement: Static bridge ring overrides are guarded at render time

WHEN `config.sessionBridgeInstanceRing` is non-empty, chart rendering MUST fail with a helpful error if `autoscaling.enabled=true`, OR if the trimmed ring entries do not exactly match the set of expected StatefulSet pod names (`<workload-name>-0` through `<workload-name>-<replicaCount - 1>`). The guard MUST validate entry values, not merely entry count: a ring with the right number of entries but wrong values (for example FQDN-style entries or a wrong name prefix) MUST be rejected, naming the missing or unexpected entries and the exact expected pod names.

#### Scenario: Static ring with autoscaling fails to render

- **WHEN** the chart is rendered with a non-empty `config.sessionBridgeInstanceRing` and `autoscaling.enabled=true`
- **THEN** `helm template` fails with an error stating the static ring is incompatible with autoscaling

#### Scenario: Static ring smaller than replicaCount fails to render

- **WHEN** the chart is rendered with `replicaCount=3` and a `config.sessionBridgeInstanceRing` listing 2 of the 3 expected pod names
- **THEN** `helm template` fails with an error naming the missing pod name

#### Scenario: Static ring with correct count but wrong values fails to render

- **WHEN** the chart is rendered with `replicaCount=2` and a `config.sessionBridgeInstanceRing` listing 2 entries that are not the expected StatefulSet pod names (for example FQDN-style entries or `codex-lb-0,codex-lb-1`)
- **THEN** `helm template` fails with an error naming the missing expected pod names and the exact ring the chart requires

#### Scenario: Static ring with an unexpected extra entry fails to render

- **WHEN** the chart is rendered with `replicaCount=2` and a `config.sessionBridgeInstanceRing` listing both expected pod names plus an entry that matches no StatefulSet pod
- **THEN** `helm template` fails with an error naming the unexpected entry

#### Scenario: Static ring covering every replica renders

- **WHEN** the chart is rendered with `replicaCount=2` and a `config.sessionBridgeInstanceRing` listing exactly both expected pod names
- **THEN** rendering succeeds

### Requirement: Documented bridge ring and advertise URL examples pass application validation

Bridge advertise-base-URL and manual instance-ring examples in the chart README MUST, after kubelet-style `$(POD_NAME)`/`$(POD_IP)` expansion with the chart's pod naming, satisfy the application's Settings validation (instance id literally present in the ring; advertise hostname replica-specific). Shared-service-hostname advertise examples and FQDN ring entries MUST NOT appear as recommended examples.

#### Scenario: README examples construct valid Settings

- **WHEN** the README example values are extracted and applied to Settings with a simulated StatefulSet pod name substituted for `$(POD_NAME)`
- **THEN** Settings construction succeeds without validation errors

### Requirement: Docker Compose deployments are declared single-replica

The shipped docker-compose files MUST document that they define a single-replica topology, that `docker compose up --scale` is unsupported, and that multi-replica deployments require the Helm chart with PostgreSQL.

#### Scenario: Compose files carry the guardrail statement

- **WHEN** `docker-compose.yml` and `docker-compose.prod.yml` are inspected
- **THEN** each carries the single-replica guardrail statement referencing the Helm chart path
