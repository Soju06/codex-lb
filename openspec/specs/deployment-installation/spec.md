# deployment-installation Specification

## Purpose

Define installation modes, smoke-test expectations, and the operator environment-variable contract at settings-load time, so the Helm chart remains portable across supported deployments and the configuration surface stays minimal (PRINCIPLES.md P2).
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

### Requirement: Helm support policy is pinned to modern Kubernetes minors

The chart MUST declare a minimum supported Kubernetes version of `1.32`, and CI MUST validate chart rendering against a `1.35` baseline instead of older legacy minors.

#### Scenario: Chart metadata declares the minimum supported version

- **WHEN** a user inspects the chart metadata and README
- **THEN** the documented minimum supported Kubernetes version is `1.32`

#### Scenario: CI validates the modern baseline

- **WHEN** CI runs Kubernetes schema validation and kind-based smoke installs
- **THEN** the validation set includes Kubernetes `1.35`
- **AND** pre-`1.32` validation targets are not treated as the support baseline

### Requirement: Application data directory resolution is configurable and container-aware

The application MUST resolve its default data directory from operator intent before container heuristics. A non-empty `CODEX_LB_DATA_DIR` value MUST be the highest-priority data directory override. When no override is configured, an existing `$HOME/.codex-lb` directory MUST remain preferred even if the process detects that it is running inside a container. The container data directory (`/var/lib/codex-lb`) MUST be used only when no override is configured, the home data directory does not already exist, and container detection is true.

#### Scenario: Explicit data directory override wins

- **GIVEN** `CODEX_LB_DATA_DIR` is configured to a non-empty path
- **WHEN** application settings are loaded
- **THEN** the configured path is used as the data directory
- **AND** the container detection result does not override it

#### Scenario: Existing home data is reused inside an interactive container

- **GIVEN** `CODEX_LB_DATA_DIR` is not configured
- **AND** `$HOME/.codex-lb` already exists
- **AND** container detection is true
- **WHEN** application settings are loaded
- **THEN** `$HOME/.codex-lb` is used as the data directory
- **AND** `/var/lib/codex-lb` is not selected

#### Scenario: Container default is preserved when no home data exists

- **GIVEN** `CODEX_LB_DATA_DIR` is not configured
- **AND** `$HOME/.codex-lb` does not exist
- **AND** container detection is true
- **WHEN** application settings are loaded
- **THEN** `/var/lib/codex-lb` is used as the data directory

#### Scenario: Related default paths follow the resolved data directory

- **GIVEN** the resolved data directory differs from the module-import default
- **AND** the database URL, encryption key file, conversation archive directory, and response-create dump directory are not explicitly configured
- **WHEN** application settings and proxy dump helpers are used
- **THEN** the default SQLite database URL points at `<data-dir>/store.db`
- **AND** the default encryption key file points at `<data-dir>/encryption.key`
- **AND** the default conversation archive directory points at `<data-dir>/conversation-archive`
- **AND** oversized response-create dumps are written under `<data-dir>/debug/response-create-dumps`

#### Scenario: Explicit related path overrides are preserved

- **GIVEN** `CODEX_LB_DATA_DIR` is configured
- **AND** one or more related paths such as `CODEX_LB_DATABASE_URL`, `CODEX_LB_ENCRYPTION_KEY_FILE`, or `CODEX_LB_CONVERSATION_ARCHIVE_DIR` are explicitly configured
- **WHEN** application settings are loaded
- **THEN** each explicitly configured related path keeps its configured value
- **AND** only omitted related paths derive from the resolved data directory

### Requirement: Docker Compose Postgres profile

The Docker Compose `postgres` profile SHALL use a persistent named volume for Postgres data.

When the profile uses Postgres 18 or newer, the service SHALL mount that named volume at `/var/lib/postgresql`, the parent directory of the image's versioned `PGDATA` path.

The Compose configuration SHALL provide an explicit one-shot upgrade profile for existing pre-18 named volumes.

The `postgres-upgrade` service SHALL pin the upgrade helper image by digest because the helper mounts the same named Postgres data volume read-write and mutates the stored database cluster.

The normal Postgres service SHALL fail before starting Postgres 18 when it detects a pre-18 root-level `PG_VERSION` marker in the mounted named volume.

The normal Postgres service SHALL fail before starting Postgres 18 when it detects a nested `/var/lib/postgresql/data/PG_VERSION` marker with a pre-18 major version.

The normal Postgres service SHALL preserve runtime command arguments when it delegates to the official Postgres entrypoint.

The operator documentation SHALL describe how to stop the old service, back up the named volume, run the upgrade profile, start Postgres, and verify the upgraded database.

#### Scenario: Existing Postgres 16 volume is guarded

- **GIVEN** the named Compose volume contains a root-level `PG_VERSION` file from a Postgres 16 data directory
- **WHEN** the operator starts the normal `postgres` service after the Postgres 18 upgrade
- **THEN** the service exits before running Postgres
- **AND** the error tells the operator to run the `postgres-upgrade` profile

#### Scenario: Upgraded or fresh Postgres 18 volume starts normally

- **GIVEN** the named Compose volume does not contain a root-level `PG_VERSION` file
- **WHEN** the operator starts the normal `postgres` service
- **THEN** the service delegates to the official Postgres entrypoint
- **AND** the Postgres 18 image initializes or opens the versioned data directory under `/var/lib/postgresql`

#### Scenario: Nested legacy data directory is guarded

- **GIVEN** the named Compose volume contains a nested `/var/lib/postgresql/data/PG_VERSION` file with a pre-18 major version
- **WHEN** the operator starts the normal `postgres` service after the Postgres 18 upgrade
- **THEN** the service exits before running Postgres
- **AND** the error tells the operator that the nested data directory must be upgraded before Postgres 18 starts

#### Scenario: Runtime command arguments are preserved

- **GIVEN** the named Compose volume does not contain a root-level `PG_VERSION` file
- **WHEN** the operator starts the normal `postgres` service with runtime PostgreSQL command arguments
- **THEN** the guard delegates those arguments to the official Postgres entrypoint

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

### Requirement: Removed tunables are fixed constants or derived values

Values that are protocol constants or internal tuning details SHALL NOT be
operator-configurable. When a previously supported `CODEX_LB_*` setting is
removed from the configuration surface, its environment variable MUST be
ignored without failing startup, and for at least one release after removal,
startup MUST emit a single warning log listing every removed setting name
found in the process environment (never the values), referencing the
simplicity principle that motivated the removal. Each subsystem affected by
a removal MUST retain at most one enable/disable setting, and the Helm chart
MUST NOT render environment variables for removed settings.

The following values MUST be fixed at their previously documented defaults:

- The OAuth protocol identity values (authorization base URL, client id,
  originator, scope, redirect URI, and callback port): they identify
  codex-lb to OpenAI exactly like the Codex CLI, and changing any of them
  breaks login.
- Background scheduler cadences (quota planner tick, automations poll,
  model registry refresh, sticky-session cleanup).
- The Codex client fingerprint (OS, architecture, terminal).
- Live-usage write coalescing (minimum write interval and queue size).
- The request-log count-cache TTL.
- Circuit-breaker tuning (failure threshold and recovery timeout).
- The images-route internals (internal host model and partial-images cap).
- The PostgreSQL pool checkout timeout (30 seconds) and pooled-connection
  recycle window (1800 seconds).
- The soft-drain/probe thresholds (primary drain threshold 85%, secondary
  drain threshold 90%, error window 60 seconds, error count 2, probe quiet
  window 60 seconds, probe success streak 3), fixed in
  `app/core/balancer/logic.py`.

The following values MUST be derived rather than configured:

- The memory-pressure warning threshold: 80% of the configurable reject
  threshold (`CODEX_LB_MEMORY_REJECT_THRESHOLD_MB`), with both disabled
  when the reject threshold is 0.
- The background-task database engine's pool size and max overflow: always
  taken from `database_pool_size` and `database_max_overflow`.

Incident-debugging trace logging SHALL be controlled by the single
`CODEX_LB_TRACE` comma-separated channel list, whose empty default disables
all trace channels. The Codex HTTP-bridge prewarm rollout scoping SHALL NOT
be operator-configurable: prewarm eligibility MUST be the
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` flag alone,
with no canary sampling percent and no API-key allow/deny cohort lists (the
removed `..._PREWARM_CANARY_PERCENT`, `..._PREWARM_ALLOW_API_KEY_IDS`, and
`..._PREWARM_DENY_API_KEY_IDS` variables are covered by the
removed-settings warning). `database_pool_size` and `database_max_overflow`
MUST remain operator-configurable settings, and `soft_drain_enabled` and
`deterministic_failover_enabled` MUST remain the failover subsystem's
enable switches.

#### Scenario: Removed env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_AUTH_BASE_URL` and `CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS`
- **WHEN** the application starts
- **THEN** startup succeeds and the fixed built-in values are used
- **AND** exactly one warning log lists both removed names without their
  values

#### Scenario: Clean environment starts without removal warnings

- **GIVEN** a deployment that sets no removed setting names
- **WHEN** the application starts
- **THEN** no removed-settings warning is logged

#### Scenario: Trace channels default to off

- **GIVEN** a default install with `CODEX_LB_TRACE` unset
- **WHEN** the proxy serves requests
- **THEN** no request-shape, payload, service-tier, or upstream trace logs
  are emitted

#### Scenario: A trace channel can be enabled for an incident

- **GIVEN** `CODEX_LB_TRACE=shape,upstream_payload`
- **WHEN** the proxy serves requests
- **THEN** request-shape and upstream-payload trace logs are emitted while
  all other trace channels stay off

#### Scenario: Removed scheduler and images env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_QUOTA_PLANNER_TICK_SECONDS` and `CODEX_LB_IMAGES_HOST_MODEL`
- **WHEN** the application starts
- **THEN** startup succeeds and the fixed built-in values are used
- **AND** exactly one warning log lists both removed names without their
  values

#### Scenario: Memory warning threshold derives from the reject threshold

- **GIVEN** `CODEX_LB_MEMORY_REJECT_THRESHOLD_MB=100`
- **WHEN** process RSS reaches 80 MiB
- **THEN** a memory warning is logged while requests continue to be served
- **AND** requests are rejected with 503 only once RSS reaches 100 MiB

#### Scenario: Memory guard stays fully disabled by default

- **GIVEN** a default install with `CODEX_LB_MEMORY_REJECT_THRESHOLD_MB`
  unset (0)
- **WHEN** the proxy serves requests under any memory usage
- **THEN** no memory warning is logged and no request is rejected for
  memory pressure

#### Scenario: Helm chart renders no removed settings

- **GIVEN** a Helm install using the chart's default values
- **WHEN** the config map is rendered
- **THEN** it contains no `CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD`,
  `CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS`, or
  `CODEX_LB_STICKY_SESSION_CLEANUP_INTERVAL_SECONDS` entries
- **AND** startup emits no removed-settings warning

#### Scenario: Removed pool and drain env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS` and
  `CODEX_LB_DRAIN_PRIMARY_THRESHOLD_PCT`
- **WHEN** the application starts
- **THEN** startup succeeds and the fixed built-in values are used
- **AND** exactly one warning log lists both removed names without their
  values

#### Scenario: Background pool sizing derives from the main pool settings

- **GIVEN** `CODEX_LB_DATABASE_POOL_SIZE=12` and
  `CODEX_LB_DATABASE_MAX_OVERFLOW=4` on a PostgreSQL deployment
- **WHEN** the application creates the background-task database engine
- **THEN** the background engine uses pool size 12 and max overflow 4
- **AND** no separate background pool sizing can be configured

#### Scenario: Drain and probe thresholds are fixed constants

- **GIVEN** a deployment with `soft_drain_enabled` left at its default
- **WHEN** an account's primary window usage reaches 85%
- **THEN** the account enters the draining health tier
- **AND** a drained account enters the probing tier only after the fixed
  60-second quiet window, regardless of any `CODEX_LB_PROBE_QUIET_SECONDS`
  value still present in the environment

#### Scenario: Removed prewarm canary env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets
  `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_CANARY_PERCENT` or
  the allow/deny list variables
- **WHEN** the application starts
- **THEN** startup succeeds and the values are ignored
- **AND** exactly one warning log lists the removed names without their
  values

#### Scenario: Prewarm eligibility is the enabled flag alone

- **GIVEN** `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED=true`
- **WHEN** a first-turn Codex bridge request arrives on a session that has
  not been prewarmed
- **THEN** the session prewarm is attempted for that request
- **AND** no request is excluded by canary sampling or an allow/deny cohort

#### Scenario: Prewarm stays off by default

- **GIVEN** a default install with no prewarm variables set
- **WHEN** Codex bridge requests are served
- **THEN** no session prewarm is attempted and visible requests record
  `prewarm_status=not_applicable`
