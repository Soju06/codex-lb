## ADDED Requirements

### Requirement: Opt-in HAProxy blue/green Compose deployment

The project SHALL ship an opt-in production Compose topology in which HAProxy exclusively owns the existing public application port and two independently named codex-lb backend slots remain private to the Compose network. The topology MUST preserve WebSocket upgrades and long-lived HTTP/SSE streams, MUST health-check backend readiness, MUST expose neither the HAProxy control interface nor backend application ports on the host, and MUST leave the existing stock Compose deployments single-replica.

#### Scenario: HA topology renders with one public front door

- **WHEN** the HA Compose files are rendered
- **THEN** HAProxy publishes the existing application port
- **AND** both application slots are reachable only on the internal Compose network
- **AND** HAProxy's runtime control interface is reachable only from inside the HAProxy container

#### Scenario: WebSocket and streaming traffic traverse HAProxy

- **WHEN** a client sends an HTTP request, an SSE request, or a WebSocket upgrade to the public port
- **THEN** HAProxy forwards the protocol without response buffering or an application-level idle timeout that is shorter than codex-lb's supported connection lifetime

#### Scenario: Stock Compose remains simple

- **WHEN** an operator uses only `docker-compose.yml` or `docker-compose.prod.yml`
- **THEN** the documented single-replica topology and public ports remain unchanged

### Requirement: Readiness-gated zero-downtime rollout command

The project SHALL provide one documented deployment command that identifies the active and inactive slots, builds or selects the requested candidate image, starts only the inactive slot, and waits for its strict readiness before changing traffic. It MUST switch new HAProxy connections to the candidate through the HAProxy runtime interface without creating a no-ready-backend gap, MUST verify the public readiness path after cutover, MUST place the predecessor in drain before stopping it, and MUST retain the candidate as the active slot across HAProxy restarts.

The runtime switch MUST raise the candidate to a positive absolute weight even
when its static configured weight is zero. It MUST NOT derive the activation
weight as a percentage of that zero baseline.

The command MUST fail closed without removing the active slot when configuration validation, image build, candidate startup, candidate readiness, HAProxy runtime mutation, or public post-cutover verification fails. It MUST serialize concurrent invocations and MUST support explicit status inspection and rollback to a still-running healthy predecessor during the drain window.

#### Scenario: Successful blue-to-green rollout

- **GIVEN** blue is active and green is inactive
- **WHEN** the operator invokes the deployment command for a healthy candidate
- **THEN** green reaches strict readiness before receiving traffic
- **AND** new connections switch to green
- **AND** blue stops receiving new connections, drains existing work within the configured bound, and is then stopped
- **AND** green is recorded as active

#### Scenario: Candidate fails before cutover

- **GIVEN** one healthy slot is active
- **WHEN** the candidate fails to build, start, or become ready
- **THEN** HAProxy continues routing new connections to the active slot
- **AND** the active slot remains running
- **AND** the command exits non-zero with the failed stage identified

#### Scenario: Cutover verification fails

- **WHEN** HAProxy accepts the runtime switch but the public readiness check fails
- **THEN** the command re-enables the predecessor for new connections when it remains healthy
- **AND** disables the failed candidate
- **AND** exits non-zero without stopping the predecessor

#### Scenario: Zero-weight candidate becomes eligible before predecessor drain

- **GIVEN** the inactive candidate has static HAProxy weight zero
- **WHEN** the deployment command performs a cutover
- **THEN** it assigns the candidate a positive absolute runtime weight
- **AND** only then assigns the predecessor weight zero
- **AND** HAProxy retains at least one eligible backend throughout the switch

#### Scenario: Operator rolls back during drain

- **GIVEN** the predecessor is still healthy and has not yet been stopped
- **WHEN** the operator requests rollback
- **THEN** new connections return to the predecessor without a no-ready-backend gap
- **AND** the candidate enters drain

#### Scenario: Concurrent rollout is rejected

- **WHEN** a second deployment command starts while another owns the deployment lock
- **THEN** the second command exits non-zero without changing containers or HAProxy state

### Requirement: Bounded zero-downtime claim

The Docker HA deployment documentation MUST define zero downtime as continuous admission for new HTTP, SSE, and WebSocket connections during a healthy cutover. It MUST disclose that an old connection can still be terminated after the configured HAProxy/application drain bound, that host or HAProxy process failure remains a single front-door failure domain, and that database migrations must remain rolling-compatible while old and new application versions overlap.

#### Scenario: Operator evaluates the availability guarantee

- **WHEN** an operator reads the Docker HA deployment documentation
- **THEN** it distinguishes healthy application cutover from host/front-proxy high availability
- **AND** states the drain-bound limitation for long-lived connections
- **AND** requires rolling-compatible migrations during version overlap

### Requirement: Repository-owned HA deployment automation

The project SHALL provide an automatically discoverable Codex skill for deployment requests targeting an already bootstrapped single-host HAProxy Compose environment. The skill MUST use the shipped HA deployment script as the only rollout mutation path, MUST inspect status before deployment, MUST wait for the bounded rollout to finish, and MUST verify public readiness and the active-slot transition afterward. It MUST NOT treat deployment as authorization to commit or push, MUST NOT replace the HA workflow with direct application-container recreation, and MUST require explicit confirmation before a first-time bootstrap that has a documented interruption.

#### Scenario: Later deployment request selects HA rollout

- **GIVEN** the HA topology has an active slot
- **WHEN** an operator asks Codex to deploy application changes on the Compose production host
- **THEN** the deployment skill runs the HA deploy command rather than directly recreating the active application container
- **AND** reports the resulting active slot and public readiness

#### Scenario: First-time bootstrap is not implicit

- **GIVEN** the HA topology has no active-slot state
- **WHEN** an operator makes a generic deployment request
- **THEN** the skill reports that HA bootstrap is required
- **AND** waits for explicit acknowledgement of the one-time interruption before bootstrapping

#### Scenario: Deployment request does not imply source-control mutation

- **WHEN** the deployment skill applies a rollout
- **THEN** it does not commit or push repository changes unless the operator separately requests that action
