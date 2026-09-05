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

### Requirement: Owned launch paths preserve raw peer before proxy projection

Every project-owned launch path for the main application MUST disable server-level proxy-header projection. The outermost application middleware MUST preserve the incoming HTTP or WebSocket `scope["client"]` before applying Uvicorn-compatible proxy projection exactly once. Downstream consumers MUST continue to observe Uvicorn's projected client and scheme. Projection MUST use `FORWARDED_ALLOW_IPS` unchanged: unset MUST trust `127.0.0.1`, empty MUST trust no peer, `*` MUST trust every peer, and explicit hosts or networks MUST retain Uvicorn's parsing and trusted-chain behavior. The change MUST NOT introduce a new setting.

#### Scenario: Owned launchers disable early projection

- **WHEN** the main application starts through the project CLI, development Compose, or a shipped direct FastAPI/Uvicorn command
- **THEN** server-level proxy-header projection is disabled
- **AND** application capture and projection run exactly once

#### Scenario: HTTP and WebSocket preserve both identities

- **WHEN** a trusted peer sends valid `X-Forwarded-For` and `X-Forwarded-Proto` headers over HTTP or WebSocket
- **THEN** the raw transport peer remains preserved
- **AND** downstream handling observes Uvicorn's projected client and protocol-appropriate scheme

#### Scenario: Forwarded allowlist behavior is unchanged

- **WHEN** `FORWARDED_ALLOW_IPS` is unset, empty, `*`, or an explicit host/network list
- **THEN** proxy projection follows Uvicorn's existing trust semantics

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

### Requirement: Response-create dump directory is bounded without configuration

The oversized response-create dump directory under `<data-dir>/debug/response-create-dumps` MUST be bounded on the base install path with no operator configuration. When the service captures an oversized `response.create` payload, it MUST NOT write a new dump if a dump for the same payload fingerprint is already stored, and after storing a dump it MUST remove the oldest stored dumps so that at most a fixed number of dump pairs remain. Each dump is a pair of a gzipped payload file and a meta file that MUST be added and removed together. Suppressing a duplicate MUST remain operator-visible in the logs, because the recurrence signal is the reason the dump path exists.

#### Scenario: Repeated identical payloads are stored once

- **GIVEN** an oversized `response.create` payload has already been dumped
- **WHEN** a retry of the byte-identical payload is dumped again
- **THEN** no additional dump pair is written
- **AND** the originally stored dump pair is retained
- **AND** the suppressed duplicate is logged with its payload fingerprint and the path of the existing dump

#### Scenario: Distinct payloads are stored separately

- **GIVEN** an oversized `response.create` payload has already been dumped
- **WHEN** a different oversized payload is dumped
- **THEN** a separate dump pair is written for it

#### Scenario: Oldest dumps are pruned once the directory is full

- **GIVEN** the dump directory already holds the maximum number of dump pairs
- **WHEN** a dump for a new payload is written
- **THEN** the oldest dump pairs are removed so the maximum is not exceeded
- **AND** each removed payload file has its meta file removed with it
- **AND** the newly written dump pair is retained

#### Scenario: Dump retention needs no setting

- **GIVEN** a default installation with no dump-related configuration
- **WHEN** oversized response-create dumps are captured over time
- **THEN** duplicate suppression and pruning apply
- **AND** no `CODEX_LB_*` setting is required to bound the directory

### Requirement: External secret references support provider-native layouts

When `externalSecrets.enabled=true`, the Helm chart MUST render an
`external-secrets.io/v1` ExternalSecret. The database URL and encryption key
MUST each accept an independent remote key and an optional JSON property. An
empty remote key MUST default to the release fullname, and the default
properties MUST preserve the existing `database-url` and `encryption-key` JSON
layout. Explicitly nulled remote reference overrides MUST render the default
layout instead of failing the template.

#### Scenario: Existing JSON secret layout remains the default

- **WHEN** external secrets mode is enabled without remote reference overrides
- **THEN** both target keys read from the remote secret named after the release
- **AND** they extract the `database-url` and `encryption-key` JSON properties
- **AND** the rendered ExternalSecret uses `external-secrets.io/v1`

#### Scenario: Individual remote secrets need no JSON property

- **WHEN** an operator configures separate absolute remote keys for the database URL and encryption key
- **AND** leaves both property values empty
- **THEN** each target key reads the complete value of its configured remote secret
- **AND** the rendered remote references omit `property`

#### Scenario: Nulled overrides fall back to the default layout

- **WHEN** an operator explicitly nulls `externalSecrets.remoteRefs` or one of its subtrees
- **THEN** rendering succeeds
- **AND** the affected target keys use the release fullname and their default JSON properties

### Requirement: Helm PostgreSQL capacity guidance accounts for both application pools

Helm sizing documentation and production-oriented values SHALL calculate maximum application PostgreSQL connections as `(databasePoolSize + databaseMaxOverflow) * 2 pooled engines * 1 supported worker * maxReplicas`. Values described as fitting PostgreSQL's default `max_connections=100` MUST reserve at least 20 raw server slots for PostgreSQL-reserved connections, the migration path's two-connection peak, administration, and transient non-application clients.

#### Scenario: Default chart reaches its HPA ceiling

- **WHEN** the default chart scales to `autoscaling.maxReplicas`
- **THEN** both application pools across all replicas require no more than 80 PostgreSQL connections
- **AND** at least 20 raw server slots remain outside the application-pool budget

#### Scenario: Production overlay reaches its HPA ceiling

- **WHEN** `values-prod.yaml` scales to `autoscaling.maxReplicas`
- **THEN** both application pools across all replicas require no more than 80 PostgreSQL connections
- **AND** at least 20 raw server slots remain available for PostgreSQL reservations, migrations, administration, and transient non-application clients

### Requirement: Helm Grafana dashboard titles are configurable

The Helm chart MUST allow operators to override the titles of packaged Grafana
dashboards by JSON filename. The default values MUST preserve the packaged
dashboard titles.

#### Scenario: Operator uses concise titles in a folder hierarchy

- **GIVEN** Grafana dashboard provisioning is enabled
- **AND** title overrides map `codex-lb.json` to `Overview` and
  `ttft-breakdown.json` to `TTFT Breakdown`
- **WHEN** the chart renders the Grafana dashboard ConfigMap
- **THEN** each dashboard JSON document contains its configured title
- **AND** dashboard UIDs and all panel definitions remain unchanged

#### Scenario: Default titles remain compatible

- **GIVEN** Grafana dashboard provisioning is enabled
- **AND** the operator does not customize dashboard titles
- **WHEN** the chart renders the Grafana dashboard ConfigMap
- **THEN** the overview title remains `codex-lb`
- **AND** the TTFT title remains `codex-lb TTFT Breakdown`
- **AND** each ConfigMap value remains byte-identical to the chart's raw-file rendering

### Requirement: Helm preStop shares the application drain deadline

The Helm lifecycle hook MUST start local drain and poll its strict status. The configured routing dwell and application deadline MUST be measured from Python preStop-helper start. The hook MUST convey its helper-anchored absolute monotonic drain deadline to the loopback drain-start endpoint; that deadline-bearing request MUST commit the one-way process barrier. The application MUST reject non-finite values, clamp the supplied deadline so it cannot exceed the configured application timeout measured from receipt, and return the effective committed absolute deadline. The hook MUST validate that response and use the earlier of its local and returned deadlines. Local drain-start request latency or an earlier process deadline MUST therefore consume that single absolute budget rather than create another period. The hook MUST exit once the dwell has elapsed with `draining=true` and `in_flight=0`, or when the effective application drain deadline is exhausted. It MUST NOT add a second fixed drain period. A start, status, or status-schema failure MUST end the hook promptly so kubelet can deliver SIGTERM as the fallback, without rolling back a barrier already accepted by the application. Kubernetes termination grace MUST be documented as beginning before helper launch, with exec/Python launch latency consuming the hard grace but not restarting or shortening the helper-anchored application budget.

#### Scenario: Routing dwell completes with no in-flight work

- **WHEN** the Python preStop helper starts the routing dwell and status reports zero in-flight work
- **THEN** the hook waits through the routing dwell measured from helper start
- **AND** the loopback drain-start request establishes the helper-start-anchored application deadline
- **AND** local drain-start request latency does not restart that dwell
- **AND** exits without waiting through the rest of the drain timeout

#### Scenario: Drain-start request cannot extend the deadline

- **WHEN** the loopback drain-start request reaches the application after helper start
- **THEN** the application uses no deadline later than the hook's supplied absolute deadline
- **AND** clamps that value to no later than its configured timeout from receipt
- **AND** commits the process barrier and returns the effective deadline
- **AND** the hook bounds all later polling by that returned deadline
- **AND** rejects a non-finite supplied deadline

#### Scenario: Work remains after routing dwell

- **WHEN** routing dwell has elapsed and status still reports positive `in_flight`
- **THEN** the hook continues polling until `in_flight=0` or the shared deadline

#### Scenario: Drain start or status fails

- **WHEN** the local drain start request, status request, or status schema fails
- **THEN** preStop exits promptly with failure
- **AND** it does not blindly sleep through another timeout

#### Scenario: Helm timing values are unsafe

- **WHEN** `config.shutdownDrainTimeoutSeconds` is shorter than `preStopSleepSeconds`
- **OR** `terminationGracePeriodSeconds` is shorter than `config.shutdownDrainTimeoutSeconds + 32`
- **THEN** chart rendering fails with a helpful timing-contract error

#### Scenario: Operator reads shutdown documentation

- **WHEN** an operator inspects Helm shutdown tuning
- **THEN** documentation states that preStop and SIGTERM share one application deadline
- **AND** distinguishes the earlier Kubernetes hard-grace start from the Python helper's application-deadline start
- **AND** uses the nested `config.shutdownDrainTimeoutSeconds` values key
- **AND** warns that an old or custom `terminationGracePeriodSeconds` from a values file, `--set`, or `--reuse-values` below `config.shutdownDrainTimeoutSeconds + 32` makes Helm rendering fail before resources are applied
- **AND** states that the minimum is the configured drain timeout plus 32 seconds, is 62 seconds at the default 30-second drain timeout, and that the chart default is 65 seconds
- **AND** directs the operator to remove the override or raise it to at least the computed minimum before installing or upgrading
- **AND** states that omitting the key under `--reuse-values` retains the stored low value, so that path must set at least the computed minimum explicitly, while adopting the chart default requires an intentional non-reuse or `--reset-values` upgrade with the key absent

### Requirement: Shipped launch paths use the pre-connection drain server

Every shipped or documented launch path for the main application MUST delegate to the project CLI so direct SIGTERM commits the application drain barrier before Uvicorn closes connections. Development Compose MUST preserve source-watch behavior without replacing the project server with Uvicorn's reload supervisor.

#### Scenario: Development Compose watches application source

- **WHEN** the development Compose service is started with watch enabled
- **THEN** it launches the main application through `python -m app.cli`
- **AND** an application source sync restarts that service
- **AND** it does not launch direct Uvicorn reload

#### Scenario: Operator follows a shipped local command

- **WHEN** an operator follows a repository-documented command for the main application
- **THEN** that command delegates to `app.cli`
- **AND** direct SIGTERM reaches the pre-connection drain server

### Requirement: Nix flake provides reproducible development and execution paths

The repository MUST provide a locked Nix flake for each supported Nix platform. The flake MUST expose the proxy as its default package and default app, and MUST expose a default development shell containing an editable project installation, all locked runtime dependencies, the `dev` dependency group, and the project package manager. The default package and development shell MUST exclude documentation dependencies and optional runtime integrations unless they are required by those outputs. The flake package and development shell MUST use Python 3.13 and MUST derive Python dependencies from the committed `pyproject.toml` and `uv.lock` files.

Because the packaged module root lives in the read-only Nix store where env files cannot exist, the packaged entry points MUST provide launch-directory `.env` / `.env.local` loading through the explicit `CODEX_LB_ENV_FILE` settings-load override (an `os.pathsep`-separated env-file path list, honored before Settings reads env files), defaulted by the package wrapper and never overriding an operator-provided value. Nix packaging MUST NOT change env-file discovery for non-Nix launch paths: without `CODEX_LB_ENV_FILE`, env files resolve relative to the installed module root and launch-directory env files are never loaded implicitly.

#### Scenario: Default package builds the proxy

- **WHEN** a user runs `nix build`
- **THEN** Nix builds a package containing the `codex-lb` and `codex-lb-db` commands
- **AND** the package contains the compiled dashboard served by the proxy root route
- **AND** the package uses the dependency versions and hashes recorded by the flake and Python lock files

#### Scenario: Default app runs the proxy CLI

- **WHEN** a user runs `nix run . -- --help`
- **THEN** the packaged `codex-lb` command prints its CLI help and exits successfully
- **AND** running `nix run .` without help arguments starts the proxy through the project-owned CLI entry point
- **AND** the packaged app loads `.env` and `.env.local` from the directory where it is launched
- **AND** an operator-provided `CODEX_LB_ENV_FILE` value takes precedence over the launch-directory default

#### Scenario: Non-Nix launch paths keep module-root env-file discovery

- **WHEN** the application is launched outside the Nix wrapper without `CODEX_LB_ENV_FILE`
- **THEN** `.env` and `.env.local` resolve relative to the installed module root
- **AND** env files in the launch directory are not loaded

#### Scenario: Development shell is editable and complete

- **WHEN** a user enters the repository with `nix develop`
- **THEN** the shell provides Python 3.13, `uv`, the project CLI entry points, runtime dependencies, and the `dev` dependency group
- **AND** Python imports resolve the project packages from the working tree so source edits take effect without rebuilding the shell
- **AND** documentation dependencies and optional metrics and tracing integrations are absent from the default shell
- **AND** `uv` is prevented from downloading Python or replacing the Nix-managed environment

#### Scenario: Flake check builds the package

- **WHEN** a user runs `nix flake check`
- **THEN** Nix builds the default package

### Requirement: Official Linux container packages locked native egress

The official Linux container build MUST compile the native egress worker from
the repository-root Cargo workspace with its committed lockfile and pinned
toolchain in an isolated Rust build stage, and MUST install only the resulting
release executable as `codex-lb-native-egress` on the runtime path. The
executable MUST support a long-lived multiplexed request protocol and reusable
reqwest client pools without requiring a sidecar or operator setting. The
runtime image MUST NOT contain the Rust toolchain or Cargo build directory.
Python wheel and source installs MUST remain valid when the executable is
absent.

#### Scenario: Container runtime exposes native helper

- **WHEN** the official Linux image is built from the repository
- **THEN** `codex-lb-native-egress` is executable on the runtime path
- **AND** it was built with the committed lockfile
- **AND** it accepts multiple request commands during one process lifetime
- **AND** Cargo and the Rust compiler are absent from the runtime image

#### Scenario: Universal Python package remains portable

- **WHEN** a wheel or source install runs on a platform without the helper
- **THEN** importing and starting codex-lb succeeds
- **AND** supported direct requests fall back to the Python transport

### Requirement: Rust migration uses one final-state workspace

The repository MUST maintain one virtual Cargo workspace at its root, one
committed application lockfile, and a pinned Rust toolchain. Production Rust
code MUST live in focused crates below `crates/`; it MUST NOT be isolated in a
temporary language or helper subtree that requires a repository-wide move when
the Python backend is retired. The protocol, reusable transport, and worker
binary MUST remain separate dependency layers, with the worker depending on
transport and transport depending on the runtime-free protocol crate. Workspace
policy MUST forbid unsafe Rust by default, deny Clippy warnings in CI, and audit
advisories, licenses, wildcard dependencies, and non-approved sources.

#### Scenario: Another backend slice migrates to Rust

- **WHEN** a cohesive Python-owned backend slice gains a Rust implementation
- **THEN** its focused crate is added under the existing root workspace
- **AND** reusable libraries do not depend on executable crates

#### Scenario: Python backend is eventually retired

- **WHEN** Rust becomes the application owner
- **THEN** the existing root workspace and crates remain at their canonical paths
- **AND** the server application is added without relocating a temporary `rust/` or `native/` tree
