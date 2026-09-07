## MODIFIED Requirements

### Requirement: Opt-in HAProxy blue/green Compose deployment

The project SHALL ship an opt-in production Compose topology in which HAProxy exclusively owns the existing public application port, the independently named `blue`, `green`, and `amber` codex-lb backends are all active during steady state, and a fourth independently named surge backend remains stopped or ineligible outside a rollout. All four backends MUST remain private to the Compose network. The topology MUST preserve WebSocket upgrades and long-lived HTTP/SSE streams, MUST health-check backend readiness, MUST expose neither the HAProxy control interface nor backend application ports on the host, and MUST leave the existing stock Compose deployments single-replica.

#### Scenario: HA topology renders with one public front door

- **WHEN** the HA Compose files are rendered
- **THEN** HAProxy publishes the existing application port
- **AND** all four application backends are reachable only on the internal Compose network
- **AND** `blue`, `green`, and `amber` have equal positive steady-state weights
- **AND** the surge backend has zero steady-state weight
- **AND** HAProxy's runtime control interface is reachable only from inside the HAProxy container

#### Scenario: WebSocket and streaming traffic traverse HAProxy

- **WHEN** a client sends an HTTP request, an SSE request, or a WebSocket upgrade to the public port
- **THEN** HAProxy forwards the protocol without response buffering or an application-level idle timeout that is shorter than codex-lb's supported connection lifetime

#### Scenario: Stock Compose remains simple

- **WHEN** an operator uses only `docker-compose.yml` or `docker-compose.prod.yml`
- **THEN** the documented single-replica topology and public ports remain unchanged

### Requirement: Readiness-gated zero-downtime rollout command

The project SHALL provide one documented deployment command that builds and starts the temporary surge backend, waits for strict readiness, and makes it eligible before replacing any steady-state backend. The command MUST replace `blue`, `green`, and `amber` one at a time from the same built image, MUST restore each backend to a positive weight only after strict readiness, and MUST keep at least three healthy backends eligible for new traffic throughout a healthy rollout of an established three-backend topology. After all three steady-state backends run the candidate image, the command MUST drain and stop the surge backend and retain `blue`, `green`, and `amber` as the active-active steady state across HAProxy restarts.

The runtime switch MUST raise a ready backend to a positive absolute weight even when its static or restored weight is zero. It MUST NOT derive the activation weight as a percentage of a zero baseline. Before stopping a backend, the command MUST first assign it weight zero so existing HTTP, SSE, and WebSocket connections may drain within the configured bound.

The command MUST fail closed without reducing the eligible healthy backend count below three through its own mutations when candidate build, startup, readiness, runtime mutation, replacement startup, or public verification fails after surge capacity is available in an established three-backend topology. Legacy one- or two-backend migrations MUST preserve at least two eligible backends after surge capacity is available, and MUST not claim a three-backend floor before it is established. It MUST serialize conflicting mutations, expose the current rollout phase through status inspection, and allow an explicit rollback request to cancel the currently visible steady-state-backend drain before that backend is stopped. A rollback after an earlier backend has already been replaced is an abort to a safe mixed-version state, not restoration of every replaced backend.

#### Scenario: Successful active-active surge rollout

- **GIVEN** `blue`, `green`, and `amber` are healthy and eligible
- **WHEN** the operator invokes the deployment command for a healthy candidate
- **THEN** the surge backend reaches strict readiness and becomes eligible before any steady-state backend drains
- **AND** `blue` is drained, replaced from the candidate image, checked, and made eligible while `green`, `amber`, and surge continue serving
- **AND** `green` is drained, replaced from the same candidate image, checked, and made eligible while `blue`, `amber`, and surge continue serving
- **AND** `amber` is drained, replaced and readmitted while `blue`, `green`, and surge serve
- **AND** surge drains and stops only after all three steady-state backends are eligible
- **AND** `blue`, `green`, and `amber` remain the recorded active-active steady state

#### Scenario: Existing single-active HA host migrates without a front-door restart

- **GIVEN** an already bootstrapped host records one legacy active slot and its running HAProxy does not yet declare the surge backend
- **WHEN** the first active-active deployment starts
- **THEN** the command registers the ready surge backend through HAProxy's runtime interface without recreating the front door
- **AND** obtains at least two eligible backends before draining the legacy active slot
- **AND** preserves the legacy two-backend floor during migration, raising the floor to three once the three-backend topology is established
- **AND** finishes with `blue`, `green`, and `amber` active and surge stopped

#### Scenario: Candidate fails before becoming eligible

- **GIVEN** at least one existing backend is healthy and eligible
- **WHEN** surge fails to build, start, register, or become ready
- **THEN** HAProxy continues routing new connections to every previously eligible backend
- **AND** no previously eligible backend is stopped or drained
- **AND** the command exits non-zero with the failed stage identified

#### Scenario: Steady-state replacement fails

- **GIVEN** surge and at least one steady-state backend are healthy and eligible
- **WHEN** a replaced steady-state backend fails to start, become ready, or pass public verification
- **THEN** the failed replacement remains ineligible
- **AND** the other steady-state backend and surge continue admitting new connections
- **AND** status exposes the incomplete rollout for operator recovery

#### Scenario: Zero-weight backend becomes eligible before predecessor drain

- **GIVEN** a ready surge or replaced steady-state backend has runtime weight zero
- **WHEN** the deployment command adds it to the serving set
- **THEN** it assigns a positive absolute runtime weight
- **AND** verifies public readiness before draining another serving backend
- **AND** HAProxy retains at least three eligible healthy backends during the healthy rollout

#### Scenario: Operator cancels a visible steady-state drain

- **GIVEN** a steady-state backend is still healthy, draining, and has not been stopped
- **WHEN** the operator explicitly requests rollback
- **THEN** the command makes that backend eligible again before retiring surge
- **AND** aborts further replacements
- **AND** if amber does not yet exist, it records retained candidate recovery and keeps surge serving until a later deploy completes that candidate without rebuilding surge
- **AND** does not claim that already replaced backends were restored to the prior version

#### Scenario: Concurrent rollout is rejected

- **WHEN** a second deployment command starts while another owns a mutation phase
- **THEN** the second command exits non-zero without changing containers or HAProxy state unless it is the explicit rollback operation allowed for the published drain phase

### Requirement: Bounded zero-downtime claim

The Docker HA deployment documentation MUST define zero downtime as continuous admission for new HTTP, SSE, and WebSocket connections during a healthy cutover and MUST define capacity-preserving rollout as retaining at least three eligible healthy application backends after surge activation in an established three-backend topology, with a two-backend floor during legacy migration. It MUST disclose that an old connection can still be terminated after the configured HAProxy/application drain bound, that an unhealthy or already degraded topology cannot guarantee three-backend capacity, that host or HAProxy process failure remains a single front-door failure domain, and that database migrations must remain rolling-compatible while old and new application versions overlap.

#### Scenario: Operator evaluates the availability guarantee

- **WHEN** an operator reads the Docker HA deployment documentation
- **THEN** it distinguishes healthy application cutover from host/front-proxy high availability
- **AND** states the three-backend capacity guarantee and its degraded-topology exception
- **AND** states the drain-bound limitation for long-lived connections
- **AND** requires rolling-compatible migrations during version overlap

### Requirement: Repository-owned HA deployment automation

The project SHALL provide an automatically discoverable Codex skill for deployment requests targeting an already bootstrapped single-host HAProxy Compose environment. The skill MUST use the shipped HA deployment script as the only rollout mutation path, MUST inspect status before deployment, MUST wait for the bounded surge rollout to finish, and MUST verify public readiness, all three steady-state backends, and surge retirement afterward. It MUST NOT treat deployment as authorization to commit or push, MUST NOT replace the HA workflow with direct application-container recreation, and MUST require explicit confirmation before a first-time bootstrap that has a documented interruption.

#### Scenario: Later deployment request selects HA rollout

- **GIVEN** the HA topology has bootstrap state
- **WHEN** an operator asks Codex to deploy application changes on the Compose production host
- **THEN** the deployment skill runs the HA deploy command rather than directly recreating an application container
- **AND** reports that `blue`, `green`, and `amber` are healthy and eligible, surge is stopped or ineligible, and public readiness succeeds

#### Scenario: First-time bootstrap is not implicit

- **GIVEN** the HA topology has no active-slot state
- **WHEN** an operator makes a generic deployment request
- **THEN** the skill reports that HA bootstrap is required
- **AND** waits for explicit acknowledgement of the one-time interruption before bootstrapping

#### Scenario: Deployment request does not imply source-control mutation

- **WHEN** the deployment skill applies a rollout
- **THEN** it does not commit or push repository changes unless the operator separately requests that action

## ADDED Requirements

### Requirement: HA resource and balancing profile

The opt-in HA topology MUST limit each application container to 3 GiB and configure a 1-GiB aggregate native WebSocket buffer budget within that limit. The two application database pools combined MUST permit at most 20 connections per replica, so four candidate replicas total at most 80 pool connections. These overrides MUST NOT alter stock deployment defaults. HAProxy MUST use least-connection balancing for new connections without redistributing an established WebSocket.

#### Scenario: Maximum candidate overlap fits the host budget

- **WHEN** three candidate base backends and surge run together
- **THEN** their combined container memory limit is 12 GiB
- **AND** their combined database pool maximum is 80 connections

#### Scenario: Existing proxy configuration is adopted without container recreation

- **WHEN** the script adopts the checked-in HAProxy configuration on an initialized host
- **THEN** it validates the configuration and saves runtime server state before requesting a graceful master-worker reload
- **AND** it verifies the new worker and public readiness before proceeding
- **AND** it MUST NOT recreate the public-facing container or hard-stop old workers
- **AND** backend drain accounting MUST conservatively use the configured drain bound while older workers may hold uncounted sessions
- **AND** unreadable session counters MUST stop replacement without stopping the draining backend

#### Scenario: Partial rollout resumes its remaining base replacements

- **WHEN** deployment resumes a recorded replacement failure
- **THEN** it reuses the already built candidate and completes the recorded remaining base slots before retiring surge
- **AND** it does not rebuild a different candidate mid-resume
