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

Every project-owned launch path for the main application MUST disable server-level proxy-header projection. The outermost application middleware MUST preserve the incoming HTTP or WebSocket `scope["client"]` before applying Uvicorn-compatible proxy projection exactly once. Downstream consumers MUST continue to observe Uvicorn's projected client and scheme. Projection trust MUST be sourced from the `forwarded_allow_ips` setting, whose primary environment name is the bare `FORWARDED_ALLOW_IPS` (for compatibility with Uvicorn deployments) and whose prefixed alias is `CODEX_LB_FORWARDED_ALLOW_IPS`; either name MAY be set in the process environment or in the env files `Settings` reads. Projection MUST use `FORWARDED_ALLOW_IPS` unchanged: unset MUST trust `127.0.0.1`, empty MUST trust no peer, `*` MUST trust every peer, and explicit hosts or networks MUST retain Uvicorn's parsing and trusted-chain behavior. The middleware MUST NOT read the process environment directly.

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

#### Scenario: Prefixed alias is honored
- **WHEN** `CODEX_LB_FORWARDED_ALLOW_IPS` is set and `FORWARDED_ALLOW_IPS` is unset
- **THEN** proxy projection trusts the aliased value with the same semantics
- **AND** the setting appears in the generated settings reference under both names

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

### Requirement: Compose Postgres service sizes /dev/shm for parallel query

The Docker Compose `postgres` service MUST set an explicit `shm_size` of at
least 1GB. Docker's default 64MB `/dev/shm` causes PostgreSQL parallel
workers to fail with `could not resize shared memory segment ... No space
left on device` once a parallel hash join spills past the segment.

#### Scenario: Compose postgres service pins shm_size

- **WHEN** `docker-compose.yml` is inspected
- **THEN** the `postgres` service declares `shm_size` of at least 1GB

#### Scenario: Parallel hash join spills past 64MB

- **GIVEN** the Compose `postgres` service is running with the declared
  `shm_size`
- **WHEN** a parallel hash join spills more than 64MB of build tuples into
  dynamic shared memory
- **THEN** the query does not fail with `could not resize shared memory
  segment`

### Requirement: Timeout invariants are validated at startup and in CI

The application SHALL define executable timeout-invariant rules over effective
startup `Settings` fields and explicitly imported code constants for verified
relationships between request budgets, TTLs, refresh deadlines, admission
waits, retry jitter, fixed refresh cadence, and durable retry-circuit state.
Each rule SHALL name the compared setting, constant, or expression; the
relation; and a one-line rationale describing the runtime failure prevented.
Unverified timeout inventory entries SHALL NOT be enforced until their code
relationship is verified.

At startup, the application SHALL validate the effective startup `Settings`
object against the rule table. This validation SHALL NOT claim coverage for
per-request `ContextVar` overrides, runtime clamps, derived effective values
computed after startup, or database/API-key/model-source timeout values loaded
after startup. By default, startup SHALL log every violation at CRITICAL and
continue. When `timeout_invariant_validation_strict` is true, startup SHALL raise
after logging the violations. The project SHALL expose a runnable CI entrypoint
that validates the same rule table, defaults to non-strict reporting, and exits
nonzero only when `--strict` is passed and any rule is violated.

#### Scenario: Default settings satisfy timeout invariants

- **WHEN** timeout-invariant validation runs against default settings
- **THEN** every enforced rule passes
- **AND** the CI entrypoint exits successfully

#### Scenario: Non-strict startup reports violations without failing

- **WHEN** effective settings violate one or more timeout-invariant rules
- **AND** strict timeout-invariant validation is disabled
- **THEN** startup validation logs every violated rule at CRITICAL
- **AND** startup may continue

#### Scenario: Strict startup rejects violations

- **WHEN** effective settings violate one or more timeout-invariant rules
- **AND** `timeout_invariant_validation_strict` is true
- **THEN** startup validation raises an error that includes the violated rule ids

### Requirement: External database network egress matches the connection source

When bundled PostgreSQL is disabled and NetworkPolicy is enabled, the Helm chart MUST permit external PostgreSQL egress on every port selected by the database connection source. When `externalDatabase.url` is the active source, its authority port or supported SQLAlchemy query ports, including percent-encoded ASCII forms, MUST take precedence and render as unique decimal Kubernetes ports. Blank query items MUST be ignored, and portless query hosts including IPv6 literals MUST inherit the authority port before defaulting to 5432; a port outside 1 through 65535 MUST fail rendering. When an existing Secret or ExternalSecret is the active source, a stale direct URL MUST be ignored and egress MUST use `externalDatabase.port` because Helm cannot inspect the secret value. A chart-generated database URL MUST use `externalDatabase.port`, defaulting both URL and egress to 5432 when the operator does not override it. Bundled PostgreSQL egress MUST continue to target its chart-managed service on port 5432.

#### Scenario: Custom external database port is rendered consistently

- **WHEN** an operator disables bundled PostgreSQL, enables NetworkPolicy, and
  sets `externalDatabase.port=6432`
- **THEN** the chart-generated database URL uses port 6432
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 6432

#### Scenario: External database port retains its default

- **WHEN** an operator disables bundled PostgreSQL and enables NetworkPolicy
  without overriding `externalDatabase.port`
- **THEN** the chart-generated database URL uses port 5432
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 5432

#### Scenario: Direct external database URL uses its explicit port

- **WHEN** an operator disables bundled PostgreSQL, enables NetworkPolicy, and
  sets `externalDatabase.url` with port 6432
- **THEN** the chart-generated Secret retains the direct database URL
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 6432

#### Scenario: Direct external database URL without a port uses the PostgreSQL default

- **WHEN** an operator disables bundled PostgreSQL, enables NetworkPolicy, and
  sets `externalDatabase.url` without an explicit port
- **THEN** the chart-generated Secret retains the direct database URL
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 5432

#### Scenario: Equivalent direct URL port forms are normalized

- **WHEN** an active direct database URL supplies its effective port through an
  authority with leading zeros, a URL-encoded query `port`, or a query `host`
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits the same
  decimal TCP port used by SQLAlchemy

#### Scenario: Portless query host inherits the authority port

- **WHEN** an active direct database URL supplies an authority port and a
  portless query `host`
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits the
  authority TCP port used by SQLAlchemy

#### Scenario: Portless IPv6 query host keeps the PostgreSQL default

- **WHEN** an active direct database URL without an authority port supplies a
  portless IPv6 query `host`
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits TCP 5432
- **AND** no IPv6 hextet is interpreted as a port

#### Scenario: Blank query items do not override effective ports

- **WHEN** an active direct database URL contains blank `host` or `port` query
  items beside a valid port source
- **THEN** the blank items are ignored
- **AND** the external PostgreSQL NetworkPolicy permits only the effective port

#### Scenario: Multihost direct URL permits every failover port

- **WHEN** an active direct database URL supplies multiple query hosts on
  different valid ports
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits every
  unique TCP port used by those hosts

#### Scenario: Secret-backed database source ignores a stale direct URL

- **WHEN** an existing Secret or ExternalSecret supplies the database URL
- **AND** an inactive direct URL declares a different port
- **THEN** the external PostgreSQL NetworkPolicy ignores the inactive URL
- **AND** its egress rule uses `externalDatabase.port`

#### Scenario: Invalid direct URL port fails rendering

- **WHEN** an active direct database URL declares a port outside 1 through 65535
- **THEN** Helm rendering fails before resources are applied

#### Scenario: Bundled PostgreSQL egress is unchanged

- **WHEN** bundled PostgreSQL and NetworkPolicy are enabled
- **THEN** the PostgreSQL egress rule targets the chart-managed PostgreSQL pods
- **AND** it permits TCP 5432

### Requirement: Operator metrics and log configuration fails closed

The application MUST accept `CODEX_LB_METRICS_PORT` only in inclusive
`1..65535` and `CODEX_LB_LOG_FORMAT` only as `text` or `json`. Invalid values
MUST produce field-specific validation errors before metrics startup or
formatter selection. Existing main/metrics collision rejection MUST remain.

Helm values schema MUST enforce the same metrics range and log-format set before
rendering/install. Valid defaults/boundaries MUST remain unchanged.

#### Scenario: Impossible metrics port is rejected

- **WHEN** metrics port is zero, negative, or above 65535
- **THEN** settings validation identifies `metrics_port`

#### Scenario: Unknown log format is rejected

- **WHEN** log format is not text or json
- **THEN** settings validation identifies `log_format`

#### Scenario: Helm rejects invalid operator values

- **WHEN** Helm metrics/log values violate the same contract
- **THEN** schema validation fails with the values path

### Requirement: Removed tunables are fixed constants, derived values, or dashboard settings

Values that are protocol constants or internal tuning details SHALL NOT be
operator-configurable, and values the dashboard runtime settings own SHALL NOT
also be operator-configurable through the environment. When a previously
supported `CODEX_LB_*` setting is removed from the configuration surface, its
environment variable MUST be ignored without failing startup, and for at
least one release after removal, startup MUST emit a single warning log
listing every removed setting name found in the process environment or the
loaded env files (never the values), referencing the simplicity principle
that motivated the removal. Once a removed name has had its warning release,
it MUST be pruned from the warning list while staying inert (`extra="ignore"`);
the warning list therefore covers only the most recent removal batch. Each
subsystem affected by a removal MUST retain at most one enable/disable
setting, and the Helm chart MUST NOT render environment variables for removed
settings.

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
- The never-tuned core tunables constantized by `constantize-core-tunables`:
  the upstream SSE event / websocket frame budget (16 MiB) and the derived
  serialized `response.create` budget (15 MiB); the OAuth exchange timeout
  (30 s), token-refresh exchange timeout (8 s), refresh-failure negative
  cache (5 s) and the token-refresh claim TTL (`max(30 s, admission wait +
  2 x refresh timeout)`, all in code); the admission wait (10 s) and the
  token-refresh (64), upstream websocket connect (128) and compact
  response-create (64) gates; the usage / reset-credits fetch timeout (10 s)
  and retry budget (2), the usage refresh interval (60 s) with its derived
  freshness horizon, the usage auth-failure cooldown (300 s) and the
  reset-credits polling interval (60 s); the always-on switches for usage
  refresh, live usage ingestion, sticky-session cleanup, the model registry
  and the quota planner scheduler (the dashboard `quota_planner_settings.mode
  = "off"` remains the only planner switch); the HTTP ingress body budgets
  (32 MiB general, 128 MiB Responses); inline image fetching (always on, no
  host allowlist); the public default image model (`gpt-image-2`); and
  proxy-generated prompt-cache-key derivation (always on). There is no
  separate upstream compact timeout: the dashboard compact request budget is
  the only cap.

The following values MUST be derived rather than configured:

- The memory-pressure warning threshold: 80% of the configurable reject
  threshold (`CODEX_LB_MEMORY_REJECT_THRESHOLD_MB`), with both disabled
  when the reject threshold is 0.
- The background-task database engine's pool size and max overflow: always
  taken from `database_pool_size` and `database_max_overflow`.

The following values MUST be owned by the dashboard runtime settings alone,
with the first-created settings row taking the column defaults (`smart`,
`1800`, `gpt-5.4-mini`, `false`) instead of an environment seed:
`http_downstream_transport_policy`, `openai_cache_affinity_max_age_seconds`,
`warmup_model`, and `http_responses_session_bridge_gateway_safe_mode`. The
retention windows (`request_log_retention_days`,
`usage_history_retention_days`) MUST be dashboard runtime settings with no
environment alias (see `data-retention`).

Incident-debugging trace logging SHALL be controlled by the single
`CODEX_LB_TRACE` comma-separated channel list, whose empty default disables
all trace channels. The Codex HTTP-bridge prewarm rollout scoping SHALL NOT
be operator-configurable: prewarm eligibility MUST be the single
`http_responses_session_bridge_codex_prewarm_enabled` switch alone, with no
canary sampling percent and no API-key allow/deny cohort lists. That switch
MUST be a dashboard runtime setting (a nullable `dashboard_settings` column of
the same name, NULL on the first-created row and never seeded from the
environment) whose
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` variable is a
deprecated alias that applies only while the column is NULL and that joins the
removed-settings warning list in the next minor release. The bridge MUST
resolve the switch before it takes a session's prewarm lock, from the dashboard
overrides the request entry point already bound, and MUST NOT add a settings
read under that lock.
`database_pool_size` and `database_max_overflow` MUST remain
operator-configurable settings, and `soft_drain_enabled` and
`deterministic_failover_enabled` MUST remain the failover subsystem's only
enable switches. Those two switches and `circuit_breaker_enabled` MUST be
dashboard runtime settings (`dashboard_settings` columns of the same name,
NULL on the first-created row; see `account-routing` and
`outbound-http-clients`) whose `CODEX_LB_*` variables are deprecated aliases
that apply only while the column is NULL and that join the removed-settings
warning list in the next minor release.

#### Scenario: Removed env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` and
  `CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS`
- **WHEN** the application starts
- **THEN** startup succeeds and the dashboard runtime values are used
- **AND** exactly one warning log lists both removed names without their
  values

#### Scenario: Clean environment starts without removal warnings

- **GIVEN** a deployment that sets no removed setting names
- **WHEN** the application starts
- **THEN** no removed-settings warning is logged

#### Scenario: Names past their warning release are silently inert

- **GIVEN** a deployment whose environment still sets names removed in an
  earlier batch, such as `CODEX_LB_AUTH_BASE_URL`,
  `CODEX_LB_QUOTA_PLANNER_TICK_SECONDS`, or
  `CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS`
- **WHEN** the application starts
- **THEN** startup succeeds, the fixed built-in values are used
- **AND** no removed-settings warning is logged for those names

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
- **THEN** it contains no `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS`,
  `CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD`, or
  `CODEX_LB_STICKY_SESSION_CLEANUP_INTERVAL_SECONDS` entries
- **AND** startup emits no removed-settings warning

#### Scenario: Dashboard-owned columns seed from their defaults

- **GIVEN** a fresh database and `CODEX_LB_WARMUP_MODEL=gpt-5.4-nano` still
  set in the environment
- **WHEN** the dashboard settings row is created for the first time
- **THEN** `warmup_model` is `gpt-5.4-mini`, `http_downstream_transport_policy`
  is `smart`, and `openai_cache_affinity_max_age_seconds` is `1800`
- **AND** the startup warning names `CODEX_LB_WARMUP_MODEL`

#### Scenario: Fresh database bootstrap ignores a removed variable

- **GIVEN** an empty database and
  `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS=64` still set in the
  environment
- **WHEN** the Alembic chain is upgraded to head
- **THEN** the seeded `dashboard_settings` row has
  `openai_cache_affinity_max_age_seconds` `1800`
- **AND** no migration reads the removed variable

#### Scenario: Removed names are matched case-insensitively

- **GIVEN** a deployment whose environment sets `codex_lb_warmup_model`
  in lowercase (which the former field honoured)
- **WHEN** the application starts
- **THEN** the startup warning lists `CODEX_LB_WARMUP_MODEL`

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

#### Scenario: Prewarm stays off by default

- **GIVEN** a default install with no prewarm variables set and the dashboard
  prewarm switch unset (NULL)
- **WHEN** Codex bridge requests are served
- **THEN** no session prewarm is attempted and visible requests record
  `prewarm_status=not_applicable`

#### Scenario: Prewarm eligibility is the enabled flag alone

- **GIVEN** the Codex session prewarm switch is on, either in the dashboard or
  through `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED=true`
  while the dashboard value is unset
- **WHEN** a first-turn Codex bridge request arrives on a session that has
  not been prewarmed
- **THEN** the session prewarm is attempted for that request
- **AND** no request is excluded by canary sampling or an allow/deny cohort

#### Scenario: Prewarm env alias applies only until the dashboard sets a value

- **GIVEN** `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED=true`
  and a `dashboard_settings` row whose
  `http_responses_session_bridge_codex_prewarm_enabled` column is NULL
- **WHEN** an operator turns the Codex session prewarm off in the dashboard
- **THEN** the next new Codex bridge session on every replica is served
  without a prewarm and without a restart, and the settings API reports
  `source: "dashboard"`
- **AND** clearing the dashboard value returns to the environment alias
  (`source: "env"`) until that alias is removed in the next minor release

#### Scenario: Resilience toggle env aliases apply only until the dashboard sets a value

- **GIVEN** `CODEX_LB_CIRCUIT_BREAKER_ENABLED=true` and a `dashboard_settings`
  row whose `circuit_breaker_enabled` column is NULL
- **WHEN** an operator sets the circuit breaker off in the dashboard
- **THEN** the next request runs with the breaker off on every replica without
  a restart, and the settings API reports `source: "dashboard"`
- **AND** clearing the dashboard value returns to the environment alias
  (`source: "env"`) until that alias is removed in the next minor release

### Requirement: Helm pre-1.13 controller-migration shim is retained through 1.26 and its removal is announced

The Helm chart MUST keep rendering the pre-1.13 controller-migration shim (the `pre-upgrade` legacy-prepare hook, the `post-upgrade` legacy-cleanup hook, the lookup-based `auto` Service selector mode and the `migration.serviceSelectorMode` value) on every chart release up to and including the 1.26 minor. The chart README MUST document the upgrade path from chart versions older than 1.13.0: what each hook does, what `migration.serviceSelectorMode` controls (the rendered selector only; the cleanup hook cuts the live Service over regardless), how to verify the cutover using the chart fullname, and that the first upgrade from a pre-1.13 release is not zero-downtime because Helm removes the legacy Deployment during the resource sync before the post-upgrade hook runs. The README MUST state the planned removal (the first minor release after 1.26) and the intermediate-upgrade path for releases still on a pre-1.13 chart before the removal ships. Removing the shim MUST be its own OpenSpec change that supersedes this requirement.

#### Scenario: Operator upgrades a release installed before chart 1.13.0

- **GIVEN** a release installed from a chart older than 1.13.0 and any chart up to the 1.26 minor
- **WHEN** the operator runs `helm upgrade`
- **THEN** the chart renders the legacy-prepare and legacy-cleanup hooks and the `auto` selector mode
- **AND** the chart README tells the operator to plan a maintenance window, how to verify the cutover, and what `migration.serviceSelectorMode` does and does not control

#### Scenario: Operator reads the deprecation notice

- **WHEN** an operator reads the chart README's `Upgrading` section
- **THEN** it states that the shim is planned for removal in the first minor release after 1.26
- **AND** it states that a release still on a pre-1.13 chart must first upgrade to a 1.13.0 - 1.26.x chart

