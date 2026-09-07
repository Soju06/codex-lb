## MODIFIED Requirements

### Requirement: Opt-in HAProxy blue/green Compose deployment

The project SHALL ship an opt-in production Compose topology in which HAProxy exclusively owns the existing public application port, the independently named `blue` and `green` codex-lb backends are both active during steady state, and a third independently named surge backend remains stopped or ineligible outside a rollout. All three backends MUST remain private to the Compose network. The topology MUST preserve WebSocket upgrades and long-lived HTTP/SSE streams, MUST health-check backend readiness, MUST expose neither the HAProxy control interface nor backend application ports on the host, and MUST leave the existing stock Compose deployments single-replica.

#### Scenario: HA topology renders with one public front door

- **WHEN** the HA Compose files are rendered
- **THEN** HAProxy publishes the existing application port
- **AND** all three application backends are reachable only on the internal Compose network
- **AND** `blue` and `green` have equal positive steady-state weights
- **AND** the surge backend has zero steady-state weight
- **AND** HAProxy's runtime control interface is reachable only from inside the HAProxy container

#### Scenario: WebSocket and streaming traffic traverse HAProxy

- **WHEN** a client sends an HTTP request, an SSE request, or a WebSocket upgrade to the public port
- **THEN** HAProxy forwards the protocol without response buffering or an application-level idle timeout that is shorter than codex-lb's supported connection lifetime

#### Scenario: Stock Compose remains simple

- **WHEN** an operator uses only `docker-compose.yml` or `docker-compose.prod.yml`
- **THEN** the documented single-replica topology and public ports remain unchanged

### Requirement: Readiness-gated zero-downtime rollout command

The project SHALL provide one documented deployment command that builds and starts the temporary surge backend, waits for strict readiness, and makes it eligible before replacing either steady-state backend. The command MUST replace `blue` and `green` one at a time from the same built image, MUST restore each backend to a positive weight only after strict readiness, and MUST keep at least two healthy backends eligible for new traffic throughout a healthy rollout. After both steady-state backends run the candidate image, the command MUST drain and stop the surge backend and retain `blue` and `green` as the active-active steady state across HAProxy restarts.

The runtime switch MUST raise a ready backend to a positive absolute weight even when its static or restored weight is zero. It MUST NOT derive the activation weight as a percentage of a zero baseline. Before stopping a backend, the command MUST first assign it weight zero so existing HTTP, SSE, and WebSocket connections may drain within the configured bound.

The command MUST fail closed without reducing the eligible healthy backend count below two when candidate build, startup, readiness, runtime mutation, replacement startup, or public verification fails after surge capacity is available. It MUST serialize conflicting mutations, expose the current rollout phase through status inspection, and allow an explicit rollback request to cancel the currently visible steady-state-backend drain before that backend is stopped. A rollback after an earlier backend has already been replaced is an abort to a safe mixed-version state, not restoration of every replaced backend.

#### Scenario: Successful active-active surge rollout

- **GIVEN** `blue` and `green` are healthy and eligible
- **WHEN** the operator invokes the deployment command for a healthy candidate
- **THEN** the surge backend reaches strict readiness and becomes eligible before either steady-state backend drains
- **AND** `blue` is drained, replaced from the candidate image, checked, and made eligible while `green` and surge continue serving
- **AND** `green` is drained, replaced from the same candidate image, checked, and made eligible while `blue` and surge continue serving
- **AND** surge drains and stops only after both steady-state backends are eligible
- **AND** `blue` and `green` remain the recorded active-active steady state

#### Scenario: Existing single-active HA host migrates without a front-door restart

- **GIVEN** an already bootstrapped host records one legacy active slot and its running HAProxy does not yet declare the surge backend
- **WHEN** the first active-active deployment starts
- **THEN** the command registers the ready surge backend through HAProxy's runtime interface without recreating the front door
- **AND** obtains two eligible backends before draining the legacy active slot
- **AND** finishes with `blue` and `green` active and surge stopped

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
- **AND** HAProxy retains at least two eligible healthy backends during the healthy rollout

#### Scenario: Operator cancels a visible steady-state drain

- **GIVEN** a steady-state backend is still healthy, draining, and has not been stopped
- **WHEN** the operator explicitly requests rollback
- **THEN** the command makes that backend eligible again before retiring surge
- **AND** aborts further replacements
- **AND** does not claim that already replaced backends were restored to the prior version

#### Scenario: Concurrent rollout is rejected

- **WHEN** a second deployment command starts while another owns a mutation phase
- **THEN** the second command exits non-zero without changing containers or HAProxy state unless it is the explicit rollback operation allowed for the published drain phase

### Requirement: Bounded zero-downtime claim

The Docker HA deployment documentation MUST define zero downtime as continuous admission for new HTTP, SSE, and WebSocket connections during a healthy cutover and MUST define capacity-preserving rollout as retaining at least two eligible healthy application backends after surge activation. It MUST disclose that an old connection can still be terminated after the configured HAProxy/application drain bound, that an unhealthy or already degraded topology cannot guarantee two-backend capacity, that host or HAProxy process failure remains a single front-door failure domain, and that database migrations must remain rolling-compatible while old and new application versions overlap.

#### Scenario: Operator evaluates the availability guarantee

- **WHEN** an operator reads the Docker HA deployment documentation
- **THEN** it distinguishes healthy application cutover from host/front-proxy high availability
- **AND** states the two-backend capacity guarantee and its degraded-topology exception
- **AND** states the drain-bound limitation for long-lived connections
- **AND** requires rolling-compatible migrations during version overlap

### Requirement: Repository-owned HA deployment automation

The project SHALL provide an automatically discoverable Codex skill for deployment requests targeting an already bootstrapped single-host HAProxy Compose environment. The skill MUST use the shipped HA deployment script as the only rollout mutation path, MUST inspect status before deployment, MUST wait for the bounded surge rollout to finish, and MUST verify public readiness, both steady-state backends, and surge retirement afterward. It MUST NOT treat deployment as authorization to commit or push, MUST NOT replace the HA workflow with direct application-container recreation, and MUST require explicit confirmation before a first-time bootstrap that has a documented interruption.

#### Scenario: Later deployment request selects HA rollout

- **GIVEN** the HA topology has bootstrap state
- **WHEN** an operator asks Codex to deploy application changes on the Compose production host
- **THEN** the deployment skill runs the HA deploy command rather than directly recreating an application container
- **AND** reports that `blue` and `green` are healthy and eligible, surge is stopped or ineligible, and public readiness succeeds

#### Scenario: First-time bootstrap is not implicit

- **GIVEN** the HA topology has no bootstrap state
- **WHEN** an operator makes a generic deployment request
- **THEN** the skill reports that HA bootstrap is required
- **AND** waits for explicit acknowledgement of the one-time interruption before bootstrapping

#### Scenario: Deployment request does not imply source-control mutation

- **WHEN** the deployment skill applies a rollout
- **THEN** it does not commit or push repository changes unless the operator separately requests that action
