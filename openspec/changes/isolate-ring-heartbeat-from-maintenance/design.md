## Context

See `proposal.md` for motivation and the delta specs for behavioral contracts. Before this change, the heartbeat loop in `app/main.py` serially performed the ring upsert, durable-ownership reconciliation, stale-operation abandonment, idle-session sweep, and cap-partition refresh. The latter phases open their own database sessions and may walk local bridge state, but have no phase deadline. A blocked phase therefore delays every later phase and the next ring renewal.

Registration is intentionally background work: startup can complete while bridge registration retries, and bridge-aware readiness stays false until registration succeeds. Shutdown currently owns one combined heartbeat task, cancels it, then marks the shared row stale. Health derives active membership from the database, but treats an empty active ring as ready after registration. The 2026-08-29 incident demonstrated both failure modes.

This change assumes the cancellation-safe owned-task primitive from `prevent-level-cancellation-busy-spin` is merged before implementation. If it is not yet on `main`, implementation will rebase after that PR rather than duplicate cancellation-deferral logic.

## Goals / Non-Goals

**Goals:**

- Keep ring renewal schedulable when any optional maintenance phase blocks, fails, times out, or is cancelled.
- Give every periodic phase one explicit owner, a deadline observation, no-overlap semantics, supervision, and bounded shutdown cleanup.
- Preserve registration retry, ring intervals, stale thresholds, cap hysteresis, bridge reconciliation/idle eligibility, and request behavior.
- Make readiness and telemetry expose the local replica's actual database-backed heartbeat health.

**Non-Goals:**

- Keep heartbeats running through total event-loop/GIL starvation; `prevent-level-cancellation-busy-spin` addresses the reproduced starvation source, while the event-loop lag monitor remains the general detector.
- Introduce a second process, worker thread, external lease service, or database schema.
- Change scheduler-leader heartbeats, API-key reservation heartbeats, upstream WebSocket heartbeats, or SSE keepalives.
- Invent new operator settings for fixed internal safety bounds.
- Redesign ring membership, cap partitioning, bridge cleanup selection, or shutdown deadlines.

## Decisions

### D1. One independently supervised owner per periodic phase

After bridge registration succeeds, the lifespan coordinator will start four separately supervised periodic owners:

1. ring heartbeat;
2. durable bridge ownership reconciliation, including stale-operation abandonment;
3. idle bridge-session sweep;
4. cap-partition membership refresh.

The two durable reconciliation passes remain sequential within one owner, but each is attempted even if the other fails; a failure is then surfaced to the owner as one bounded `durable_ownership` outcome. This preserves the merged stale-operation cleanup without adding another worker or metric label. Heartbeat registration, renewal, and stale-marking use the already initialized background database factory, while cap refresh and durable bridge maintenance retain the request factory. This existing two-pool split reserves local pool admission for heartbeat when both production-sized request-pool connections are occupied by optional bridge work without adding a PostgreSQL engine or changing connection-budget math. Each owner uses the existing ten-second cadence, but no owner awaits another. Registration still performs the initial ring write and readiness transition; the cap-partition owner runs its first refresh immediately after registration while the other owners wait for their first cadence boundary, so a blocked initial refresh cannot delay renewal. A supervisor catches ordinary phase failures, consumes task exceptions, records the outcome, and schedules the next eligible cycle. If the periodic worker itself exits unexpectedly while lifespan remains active, its supervisor logs the exit and restarts it after a small fixed backoff.

Alternatives rejected:

- `gather()` or `TaskGroup` around unsupervised loops: an unexpected failure in one child can cancel siblings, recreating heartbeat coupling.
- Fire-and-forget tasks created on each heartbeat tick: they permit unbounded overlap and lose shutdown ownership.
- A dedicated OS thread/process: much larger lifecycle and database-pool complexity, and it still would not justify bypassing the asyncio architecture for cooperative database I/O.

### D2. Deadline observation never creates overlapping phase work

Each periodic owner creates at most one child task for its phase and observes it with `asyncio.wait()` against a fixed internal deadline. The initial bounds are five seconds for a heartbeat attempt and thirty seconds for optional maintenance. These are implementation constants, not settings: the heartbeat bound leaves retry room inside the existing 30-second stale threshold, while optional phases are isolated and can safely have a wider diagnostic bound.

A deadline reports `timeout` but does not discard the child or start a replacement while it is still unfinished. The owner keeps a strong reference, observes late completion, and resumes normal cadence only after the child settles. On shutdown the child is cancelled and drained through the shared cancellation-safe task primitive within the already committed shutdown budget; if the global shutdown deadline forces abandonment, the task remains in the lifecycle-owned registry until database teardown.

Alternatives rejected:

- `asyncio.wait_for()`: it can wait indefinitely for cancellation cleanup and obscures whether the sole phase owner still runs.
- Starting a new heartbeat after every timed-out attempt: upserts are idempotent, but repeated wedged sessions would create unbounded database work and teardown races.
- Adding timeout settings: fixed safety bounds have one valid default and do not justify expanding the operator surface.

### D3. Cadence is anchored to monotonic scheduled starts

Each owner tracks its next due time with the event loop's monotonic clock. Fast completion sleeps only until the next scheduled start; slow completion never causes catch-up bursts. After a late completion, the next due time advances to the first future cadence boundary. This avoids cumulative `sleep(interval) + phase_duration` drift without replaying missed cycles.

The registration loop keeps its existing exponential retry. Periodic workers do not start until registration succeeds, and successful registration remains the readiness gate.

### D4. Readiness uses database-backed local membership, including an empty ring

The health query will return active members plus the probed replica's own row even when that row is stale. `BridgeRingInfo` gains nullable `heartbeat_age_seconds`, computed from one captured UTC instant and clamped to zero for future-skewed timestamps. The active-ring fingerprint and size continue to use only fresh rows.

Readiness retains the existing empty-ring exemption after registration. A stale single replica therefore remains routable while its heartbeat age and inactive membership remain visible. Tightening that policy is deferred to a separate owner-approved change, as requested in PR #2133. Registration-incomplete and ring-metadata-error precedence, bridge-disabled readiness, and `/health/live` remain unchanged.

Alternatives rejected:

- Process-local heartbeat success as the readiness authority: it can disagree with the committed row siblings route against.
- Making liveness fail: kubelet restarts based on ring/database transients would conflate process death with routing readiness.

### D5. Observability uses an age field, timestamp gauge, and bounded labels

`/health/ready` exposes `bridge_ring.heartbeat_age_seconds`. Prometheus adds:

- `codex_lb_bridge_ring_heartbeat_last_success_timestamp_seconds` (gauge);
- `codex_lb_bridge_ring_heartbeat_failures_total` (counter);
- `codex_lb_bridge_ring_maintenance_total{phase,outcome}` (counter).

The maintenance label sets are fixed by the spec. The timestamp gauge uses live-max multiprocess aggregation because sibling workers sharing one instance id all renew the same row; the latest successful worker represents the row's effective renewal. Operators derive process-wide age as `time() - gauge`, while readiness reports the database-backed age directly.

Failure logs include consecutive failure count and last-success age; the first success after failures emits a recovery log. Maintenance failures/timeouts and supervisor restarts include fixed phase/outcome fields and elapsed time, with no request or affinity identifiers.

### D6. Shutdown cancels periodic owners before stale-marking

The registration/periodic coordinator remains rooted by lifespan. Shutdown cancels registration and starts periodic cleanup immediately under one monotonic deadline, with no additive grace period. Once registration and heartbeat have stopped, lifespan attempts `mark_stale()` even if maintenance remains active: those maintenance owners cannot write the ring row. Full drainage is still required for the SQLite clean marker. Process-level ring supervisors use their own lifecycle primitives rather than request-scoped timing seams; their allowances remain explicit in the architecture guard.

Partial startup is handled explicitly: shutdown may cancel registration before any periodic owner exists. All cleanup paths are idempotent, and no phase shares an `AsyncSession` with another task. A registration or periodic owner that does not settle inside the shutdown bound suppresses the SQLite clean-shutdown marker, so the next startup does not trust a teardown that raced live database work.

## Risks / Trade-offs

- **[Stale single-replica readiness remains successful]** → Preserve the existing routing policy; expose heartbeat age and metrics without silently removing the only replica from traffic.
- **[A timed-out phase can remain alive beyond its diagnostic bound]** → Keep exactly one tracked owner, prohibit overlap, log late completion, and cancel/drain it before database teardown.
- **[Four supervisors add lifecycle complexity]** → Use one shared periodic-owner implementation with table-driven phase definitions and focused ownership/shutdown tests rather than four copied loops.
- **[A maintenance timeout may be normal on an unusually large local registry]** → The timeout is diagnostic, not abandonment; work continues under the same owner and later completion is recorded.
- **[Clock skew can produce a negative database age]** → Clamp health age to zero; staleness still uses the existing database timestamp comparison.
- **[A completely starved event loop still misses all periodic work]** → Retain event-loop lag monitoring and land the cancellation-spin fix first; task separation only solves cooperative phase coupling.
- **[Concurrent database phases increase short-lived session concurrency]** → Each phase owns its own session, fan-out is fixed at four, heartbeat uses the existing background pool while optional bridge database phases use the request pool, and no phase is duplicated while pending.

## Migration Plan

1. Land `prevent-level-cancellation-busy-spin`, then rebase this implementation on current `main` and any merged spool-cleanup lifecycle changes.
2. Add the shared periodic owner/supervisor and deterministic unit tests before rewiring lifespan.
3. Split the current serial loop, update health/readiness and metrics, and run health, ring, bridge lifecycle, cap partition, shutdown, SQLite, and PostgreSQL-focused suites.
4. Build a candidate image and soak it against a verified database copy with injected blocked maintenance, heartbeat failure/recovery, and shutdown during an overdue phase.
5. Roll out normally while monitoring heartbeat timestamp/age, maintenance outcomes, readiness, ring size, and event-loop lag. Roll back the application image if readiness flaps or periodic work leaks; no data or configuration rollback is required.
