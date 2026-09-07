## Context

See `proposal.md` for motivation and `specs/deployment-installation/spec.md` for the observable contract. The deployed topology currently has two declared HAProxy servers but normally keeps only one eligible. Existing hosts persist a legacy `active-slot` marker and may keep running the old two-server HAProxy configuration while repository files change. Application replicas share PostgreSQL, encryption material, and dynamic bridge-ring membership; long-lived connections cannot be moved between processes.

## Goals / Non-Goals

**Goals:**

- Keep two application processes eligible in steady state and during every healthy post-surge replacement step.
- Build the candidate image once and use the identical image for surge, blue, and green.
- Migrate an already running two-server HAProxy without restarting its public listener.
- Keep runtime state inspectable and make interruption of a published base-slot drain safe.

**Non-Goals:**

- Provide host-level or HAProxy-process high availability.
- Move live WebSocket/SSE connections between backends.
- Guarantee full two-backend capacity before surge activation when the existing topology is already degraded or legacy single-active.
- Restore every previously replaced container to its old image during an abort.

## Decisions

### Two steady-state backends and one stopped surge backend

`server-blue` and `server-green` use equal static weights. `server-surge` shares their application configuration and bridge ring but has static weight zero and is stopped outside deployment. Keeping the third process temporary avoids permanently increasing memory and background-task overhead while providing two serving processes during each replacement.

Running three permanent replicas was rejected because it spends surge resources outside rollouts. Rolling only two replicas was rejected because one process would carry all new traffic while its peer drains or restarts.

### Build on surge, then replace base slots from the same image

The script builds and starts surge first. After readiness and activation, it recreates blue and green sequentially without rebuilding, so both consume the exact image produced for surge. Each old base slot receives absolute weight zero before drain and stop; each replacement receives absolute weight one only after direct readiness succeeds.

Building independently per slot was rejected because source or dependency drift between builds could produce a mixed candidate release.

### Runtime registration bridges legacy HAProxy configuration

The checked-in HAProxy configuration declares surge for future restarts. On an existing process whose loaded configuration lacks surge, the script resolves the running surge container's internal IPv4 address and uses HAProxy 3.2's dynamic-server Runtime API to register it disabled with health checks. It then enables health checking and the server before applying a positive weight. The runtime snapshot is refreshed after registration; a future HAProxy restart reads the checked-in static surge declaration.

Until that restart occurs, the runtime-added server has no DNS identity. Each later rollout therefore detects the missing server FQDN and refreshes the dynamic server address after recreating surge, before readiness or traffic activation.

Recreating HAProxy for the topology migration was rejected because rebinding public port `2455` would violate the no-front-door-restart requirement.

### Preserve the bootstrap marker with a new steady-state value

`.codex-lb-ha/active-slot` remains the bootstrap marker for compatibility with existing automation. Legacy values `blue` and `green` are accepted for the first migration. A completed active-active rollout writes `blue,green`. Status labels this as the serving topology rather than implying one active slot.

Introducing a new mandatory state path was rejected because it would make existing initialized hosts appear unbootstrapped.

### Published drain phases allow bounded aborts

Before draining blue or green, the script records that slot in the existing phase file, snapshots HAProxy state, releases the deployment lock, and waits for sessions to reach zero or the bound. An explicit rollback can reacquire the lock, re-enable that still-running slot, mark the rollout aborted, and retire surge. The deploy process observes the changed phase and stops without replacing more slots. Once a slot has stopped, restoring its old image is outside this rollback guarantee.

### Dynamic bridge membership owns overlap routing

All newly created services statically list blue, green, and surge, while active bridge membership continues to come from shared PostgreSQL heartbeats. This lets legacy replicas discover surge during the first overlap and removes surge from ownership selection after it stops and its membership expires.

## Risks / Trade-offs

- [Three simultaneous application processes can exceed host memory] → Validate host headroom before starting surge and stop surge after the rollout; keep per-container memory limits explicit.
- [A legacy HAProxy rejects dynamic server creation or retains an old surge IP] → Validate the required Runtime API response, refresh address-only dynamic servers on every surge recreation, and leave all previous weights unchanged on failure.
- [A failed replacement leaves a mixed-version topology] → Keep the other base slot and surge eligible, expose the phase in status, and require a subsequent deploy of the desired revision.
- [Persistent HTTP connections can continue issuing requests while their server drains] → Bound the drain and preserve application SIGTERM cleanup; document that the oldest connections can be terminated at the bound.
- [Runtime and persisted server state diverge] → Snapshot after each serving-set mutation and validate the final blue/green positive, surge-zero invariant.
- [Rolling overlap crosses database contracts] → Continue requiring expand/contract migrations compatible with every overlapping application version.

## Migration Plan

1. Render the three-service Compose and HAProxy configurations without recreating the running front door.
2. On the first deploy, accept the legacy single-slot marker, start surge, dynamically register it if absent, and make it eligible beside the legacy slot.
3. Start or replace the inactive base slot from the candidate image, then roll the legacy active slot; finish with both base slots eligible.
4. On later deploys, start surge and sequentially roll blue then green while preserving two eligible backends.
5. Drain and stop surge, snapshot final HAProxy state, and write `blue,green` to the bootstrap marker.
6. If a visible base drain is explicitly rolled back, re-enable that base first, disable and drain surge, and abort remaining rollout steps. If a later stage has already replaced a base, retain the safe mixed-version topology and deploy a chosen revision next.
