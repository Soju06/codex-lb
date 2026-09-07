## Context

See proposal.md for motivation. The helper stdout reader multiplexes all native requests; each WebSocket has raw-event and decoded-message queues. Its pump also settles send acknowledgements, so awaiting space in a full message queue can deadlock send/receive. The current deployment state machine assumes two base slots and supports legacy markers.

## Goals / Non-Goals

Goals: preserve ordered accepted messages, protect memory across queue layers, isolate slow sockets, retain control progress, and increase opt-in HA capacity without broadening production authorization.

Non-goals: unlimited buffering, replay after visible output, cross-account continuation, upstream quota changes, changes to native HTTP buffering, guaranteed production RPS, database schema changes, or multi-worker instances sharing a bridge identity.

## Decisions

- Account queued object sizes (including container/payload overhead) against a shared per-socket 128-MiB budget and a per-helper budget. Default helper budget is 256 MiB; the 3-GiB HA profile overrides it to 1 GiB. The setting is necessary because stock 1-GiB containers must not inherit the larger host-specific budget. Raw and decoded queues share accounting; dequeue and close release charges. Processing/JSON/subprocess overhead remains outside this queue budget and inside the container headroom.
- Keep the control-aware pump non-blocking, yield after bounded batches in reader and pump, and terminate only an over-budget socket with explicit byte diagnostics. Accepted queued messages remain ordered ahead of the failure. Cancellation tasks are tracked and awaited/cancelled at helper shutdown. Local consumer pressure is account-neutral, preserving existing settlement and no-replay-after-output rules.
- Use blue/green/amber plus surge, equal positive base weights, 3-GiB limits. Each replica overrides pool size to 8 and overflow to 2 for each of two engines: 20 per replica, 80 during four-way overlap. PostgreSQL's 100-connection limit is unchanged. Old-version pools during first migration remain an operational risk and must be monitored; the 80 bound applies once candidate configuration is installed.
- Parameterize base-slot sequencing and persist remaining slots as a comma-separated list. A normal 3+1 rollout keeps three eligible; legacy migration preserves the existing two-backend floor and adds amber after blue/green have adopted smaller pools. Dynamically register missing servers and refresh addresses for runtime-added servers after container recreation.
- Validate the checked-in HAProxy configuration, snapshot runtime state and use the Docker image's master-worker graceful reload to adopt leastconn and the static fourth member. Preserve old workers for existing connections, verify the new worker PID/readiness, and never recreate the public listener container. Fixed server IDs preserve existing state associations.
- Old-worker sessions are invisible to the new worker's server counters, so drains conservatively use the full bound while old workers remain. Missing counters stop replacement. Legacy rollback before amber exists records a retained candidate instead of allowing the next deployment to rebuild live surge. A replacement already readmitted before a persistence error is reused only after its image matches surge; recovery never force-recreates an eligible backend.
- The settings-reference ratchet increases from 133 to 134 only for the operator-approved memory-budget override described above. The generated reference and environment validation tests are updated together; no new `.env.example` entries or top-level README sections are added.

## Risks / Trade-offs

- A byte limit is not whole-process RSS → reserve 2 GiB/backend outside the HA queue budget and measure RSS under load.
- Three active backends do not split an already-open hot WebSocket → fix scheduler fairness first and benchmark multiple independent sockets.
- Unlimited native HTTP queues remain separate existing work → do not claim all transport memory is bounded.
- Initial pool migration and build processes can exceed the steady-state estimate → report preflight resources, build before replacement, monitor pool waits and RSS; no blind database restart.
- Graceful proxy reload retains old workers → include an actual local reload check and avoid forced worker shutdown; report drain-bound limitations.
- More replicas partition account-local caps → keep unique ring identities and unchanged cluster-wide quota semantics.

## Migration Plan

Validate fake-Docker failure/recovery paths and local native-helper burst tests first. Update SSOT, published docs and deployment skill together. Production deployment remains a separate operator action through the script. Rollback only cancels a visibly draining healthy base slot; it does not undo earlier replacements. Benchmark 100/300/500 mocked concurrent sessions without paid upstream calls before claiming throughput.
