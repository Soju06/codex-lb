## Context

See `proposal.md` for motivation. Stock Compose publishes the single `server` container on ports 2455 and 1455. The application already supplies strict readiness at `/health/ready`, graceful SIGTERM admission drain, PostgreSQL-backed replica coordination, live bridge-ring discovery, owner forwarding, and an encryption-key consistency sentinel.

Compose cannot safely replace a container that owns the public port without a bind gap. A stable front proxy solves that application-cutover gap, but running old and new application containers simultaneously changes the topology to multi-replica and therefore activates the invariants in `replica-operations`.

## Goals / Non-Goals

**Goals:**

- Preserve port 2455 and keep accepting new connections while a healthy replacement becomes ready.
- Let existing connections remain on the predecessor while new connections go to the candidate.
- Provide deterministic rollback/failure behavior and machine-testable deployment artifacts.
- Keep the stock zero-config Compose path unchanged.

**Non-Goals:**

- Surviving host, Docker daemon, or HAProxy container failure.
- Multi-node HA or automatic application rollback after the predecessor has been stopped.
- Making incompatible database migrations safe.
- Solving the fixed localhost OAuth callback port for simultaneous account onboarding; port 1455 remains outside this HA overlay and can be published separately only for the active slot when required.

## Decisions

### Use a separate opt-in Compose project topology

The HA topology lives under `deploy/compose/` and defines `haproxy`, `server-blue`, and `server-green`. Only HAProxy publishes `2455`; both application slots expose `2455` on a dedicated internal edge network and separately join the existing data network for PostgreSQL. HAProxy has a fixed private edge address, strips client-supplied forwarding headers, and supplies the real socket client address; the application slots trust forwarding only from that fixed address. This preserves the public API address and client attribution while preventing port collisions and forwarded-header spoofing. Modifying stock Compose was rejected because HA requires PostgreSQL and additional operational machinery, violating its single-instance/zero-config contract.

### Keep both backend slots declared and use runtime server state

HAProxy has two statically named backend servers. At steady state the active slot has positive weight and the inactive slot has weight zero. HAProxy continues health-checking a zero-weight candidate without assigning it new traffic. After it is healthy, the deployment controller raises the candidate weight before setting the predecessor weight to zero through the private runtime listener. Weight zero stops new assignments while established connections continue. Dynamic service discovery was rejected because it does not express an operator-controlled blue/green cutover boundary.

Candidate activation uses an absolute positive runtime weight. A percentage is
not valid here because HAProxy evaluates it relative to the server's static
weight, and the inactive slot is intentionally declared with static weight
zero.

### Route all new connections to exactly one slot

The workflow never load-balances ordinary traffic across versions: the candidate becomes the only ready server before the predecessor enters drain. This reduces mixed-version exposure and makes rollback deterministic. Cross-replica bridge owner forwarding still protects hard continuity during the overlap and drain period. Hash-based front-door affinity was rejected because HAProxy cannot reliably extract every continuity signal from WebSocket/SSE traffic at connection time, and the application already owns the authoritative forwarding/fencing logic.

### Use a host deployment script with container-scoped control access

The shipped POSIX shell script drives the same explicit Compose file and project name used by the operator. It performs direct candidate readiness from inside the HAProxy container and pipes runtime commands to a loopback-only HAProxy stats listener through `docker compose exec`; neither backend ports nor the control listener are published. This avoids granting a long-lived controller container Docker-socket authority. A controller service was rejected because mounting the Docker socket would create a larger persistent privilege boundary for behavior that is only needed during deployment.

The script serializes invocations with a project-scoped `flock`. Before a rollout, the recorded active slot must agree with live HAProxy readiness and weights or the script refuses an ambiguous cutover. After every successful state transition it snapshots `show servers state` into a gitignored bind-mounted state file, and HAProxy loads that file at startup so a proxy restart preserves the selected active slot.

### Validate prerequisites before overlap

The script resolves Compose configuration before mutation and requires a PostgreSQL URL, leader election not disabled, a shared data/encryption-key volume, unique fixed instance IDs (`server-blue`, `server-green`), and matching internal advertise URLs. Both containers run the stock image entrypoint/CLI. Missing or conflicting state fails before candidate startup.

### Use two-stage verification and bounded drain

The inactive slot must pass `/health/ready` directly before HAProxy state changes. After cutover, the same path is verified through public HAProxy. On failure, the script returns the predecessor to `ready` and the candidate to `maint`. On success it waits for HAProxy sessions on the predecessor to reach zero up to the configured deployment drain timeout, then sends SIGTERM so the application's bounded drain completes before Compose stops the container.

### Make the safe path discoverable as a repository skill

A project-owned `codex-lb-ha-deploy` skill treats an explicit request to deploy application changes on the bootstrapped single-host Compose production environment as authorization to run the existing HA script. It inspects status first, delegates all mutation to that script, waits through the bounded drain, and verifies both the active-slot transition and public readiness. The skill never substitutes direct `docker compose up` commands, never commits or pushes implicitly, and requires separate confirmation before a first-time bootstrap because bootstrap has a one-time port-rebind interruption. Keeping orchestration in the existing tested script avoids creating a second deployment implementation in agent instructions.

## Risks / Trade-offs

- **[HAProxy is still one container on one host]** → Document this as zero-downtime application deployment, not front-door high availability; use Kubernetes/multiple hosts for host-level HA.
- **[Existing WebSockets can outlive the deployment drain window]** → Default to a generous explicit bound, report remaining sessions, and disclose that bounded shutdown may terminate them.
- **[The deployment script controls Docker and therefore has host-level authority]** → Require the same operator access already needed for Compose deployment, keep all resolved targets fixed to the HA project/services, and never interpolate a user-provided service name.
- **[Rollback after predecessor stop requires another rollout]** → Provide rollback only during the visible drain window and keep the active-slot record deterministic afterward.
- **[Old/new schema incompatibility can break both slots]** → Require expand/contract rolling-compatible migrations and fail candidate readiness if startup migration or schema checks fail.
- **[OAuth callback cannot be transparently shared by two app slots]** → Keep 1455 out of the HA proxy topology and document active-slot-only publication as a maintenance operation.

## Migration Plan

1. Configure PostgreSQL and back up the shared codex-lb data volume.
2. Stop the stock container currently binding port 2455 once, then start the HAProxy topology with its first backend; this initial topology migration has a one-time interruption.
3. Use the shipped deployment command for later blue/green updates; those healthy cutovers retain continuous admission.
4. To leave the topology, stop HAProxy/backends and restart stock Compose on port 2455.
