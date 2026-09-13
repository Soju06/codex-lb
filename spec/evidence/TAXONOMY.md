# codex-lb concurrency bug taxonomy

Evidence base: I mined the full local `origin/main` history in this worktree after `git fetch origin`. As of this run `git log --oneline origin/main | wc -l` reports 1063 commits, not the approximate 1600 in the prompt; the taxonomy below is therefore scoped to the complete fetched `origin/main` visible here. I used keyword unions over fix/test/deflake subjects and read representative diffs for the strongest chains, especially reservation leaks, bridge continuity, timeout budgets, drain/shutdown, and cross-replica ownership.

The CSV companion is `taxonomy.csv`; it has one classified row per evidence commit. Large aggregate commits are assigned to their dominant class in the CSV, while cross-class recurrence is called out in prose below.

## Lease and reservation leaks

Mechanism: a request acquires an API-key reservation, admission semaphore, account stream lease, or bridge lease and an error, retry, cancellation, header failure, detached persistence failure, or close/reacquire race bypasses the matching release/finalize path. The worst form is not a simple leak but a lost capacity token: later traffic sees false quota exhaustion or an account stream cap until expiry or restart.

Affected components: `app/modules/proxy/service.py`, `app/modules/proxy/_service/api_key_usage.py`, `app/modules/proxy/_service/http_bridge/*`, `app/modules/proxy/_service/websocket/mixin.py`, `app/modules/api_keys/*`, and SQLite-backed reservation recovery.

Why it recurs: the proxy has many terminal exits. Streaming, compact, HTTP bridge forwarding, WebSocket reconnect, header validation, detached persistence, and grouped terminal-error handling all used to own cleanup locally. Every new path had to remember whether it owned the reservation or inherited it from a caller. Cancellation makes this nastier because `CancelledError` bypasses ordinary `except Exception` cleanup.

Evidence: `592d47b3` moved stream settlement to an exactly-once retry wrapper; `fe625bfb` fixed a later compact preflight budget-exhausted leak in the same family; `783665b9` fixed header-failure reservation release; `93490404` and `f06173e7` fix stream lease leaks on idle bridge and WebSocket disconnect.

Machine-checkable invariant: every acquired lease/reservation/semaphore has a unique owner token and reaches exactly one of `finalized`, `released`, or `transferred` before the request/turn/session reaches a terminal state; cancellation is just another terminal transition.

## Timeout budget mismatch and stuck streams

Mechanism: one timeout budget is applied to the wrong phase, or a phase has no effective timeout. Healthy long streams were killed by request budgets, stuck pre-header upstream connections waited for hours, idle client-facing SSE streams left clients hanging, and timeout classification changed under scheduler jitter.

Affected components: `app/core/clients/proxy.py`, `app/core/clients/http.py`, `app/core/utils/sse.py`, `app/modules/proxy/_service/streaming/*`, `app/modules/proxy/_service/websocket/*`, and HTTP bridge prewarm/keepalive code.

Why it recurs: codex-lb has at least four clocks: connect timeout, upstream idle timeout, total request budget, and client-facing keepalive. They guard different resources, but patches repeatedly collapsed them into one number or forgot to carry the effective deadline across reconnect/retry paths.

Evidence: `aa65e97d` decoupled stream duration from request budget; `de2c5fc0` extended WebSocket stream budget after the same long-turn class returned; `af5051f8` bounded upstream connections that never sent response headers; `66302c3e` added SSE keepalives; `17e8abc0` preserves timeout classification under jitter.

Machine-checkable invariant: for every request attempt, `connect_deadline <= first_byte_deadline <= request_deadline`, all waits are bounded by the remaining request deadline, and an active stream is never failed by total budget while it is making permitted progress.

## Stale continuity anchor and owner mapping

Mechanism: stale `previous_response_id`, durable bridge rows, sticky session mappings, turn-state aliases, or codex_session owner bindings outlive the owner/session that made them valid. The next request then either loops on a dead anchor, forks context by dropping the anchor, routes to an unreachable owner, or repeats a full replay unsafely.

Affected components: durable bridge repository/coordinator, HTTP bridge owner forwarding and session registry, WebSocket replay handling, sticky selection, response continuity helpers, and startup cleanup.

Why it recurs: continuity state is split between client payloads, proxy-injected anchors, durable DB rows, in-memory sessions, sticky mappings, and upstream server state. A fix that is correct for one origin of `previous_response_id` can be unsafe for another. Recovery also needs to know whether a resend is full-context and account-neutral.

Evidence: `85802e64` prevented recovery from converting a transient bridge loss into `previous_response_not_found`; `4fccca1e` hardens continuity recovery and safe replay broadly; `48f083ef` and `4c04e538` handle stale WebSocket/durable anchors; `b1d27bc6` and `9b40f746` purge stale owner mappings and durable rows.

Machine-checkable invariant: a continuity anchor is usable only if its owner epoch is current, its account/model capability lineage is compatible, and its payload-origin class permits recovery; otherwise the state machine must either clear it under a fenced proof or fail with a retryable owner-loss error without mutating the client anchor.

## Cancellation and terminal stream contamination

Mechanism: terminal events and cancellation are not isolated per request. A cancelled `/v1/responses` stream can contaminate the next stream, terminal SSE frames can be followed by non-terminal frames, downstream cancellations can be missed, and teardown can rewrite or lose the true terminal reason.

Affected components: proxy API streaming responses, HTTP bridge demux, WebSocket terminal handling, middleware body-read/disconnect handling, request logging, and tests that expose close-order flakes.

Why it recurs: asyncio cancellation is control flow, not just an error. If the code shares queues, request state, or cleanup futures across turns, cancellation of one consumer can leave producer tasks alive or leave terminal metadata in shared state. Tests often race because they assert after close without proving the cleanup task has reached its terminal state.

Evidence: `c9da4974` eliminates cancel/retry stream contamination; `1089ab5d` records early downstream cancellations; `6e8fa56f` shields persistence from cancellation while preserving cancellation semantics; `89ffe7d4` is a test-only deflake that makes close ordering explicit.

Machine-checkable invariant: after a request emits a terminal event or observes downstream cancellation, no producer associated with that request may enqueue frames to any later request, and the terminal reason is immutable until persistence finishes.

## Shutdown drain and background task lifecycle

Mechanism: process shutdown releases ownership or exits while work is still active. WebSocket turns, audit/fleet tasks, scheduler leadership, detached token refreshes, and background SQLite handles can survive past the lifetime of the process or DB session that owned them.

Affected components: `app/core/shutdown.py`, `app/core/prestop.py`, `app/core/server.py`, WebSocket service, audit/fleet APIs, scheduler leadership, detached refresh tasks, and background refresh maintenance.

Why it recurs: the proxy has long-running foreground streams and short control-plane tasks. Both need a shared drain protocol, but early code treated shutdown as best-effort cancellation. Detached tasks are especially easy to start with borrowed DB sessions or no ownership registration.

Evidence: `66b9196d` drains active WebSocket turns and bounds post-drain exit; `ec36ef60` drains audit and fleet tasks; `13d3a321` ties scheduler leadership to run-if-leader gating and shutdown release; `3bdc9dea` makes detached token refresh own its DB session.

Machine-checkable invariant: once draining starts, no new externally visible turn or singleton task is admitted, all registered tasks reach `completed`, `cancelled`, or `abandoned_with_owner_release` before the shutdown deadline, and no borrowed DB session is used by a detached task.

## Cross-replica single-owner coordination

Mechanism: work that must have one winner is performed by multiple replicas or processes: Alembic migrations, token refresh, bridge lease renewal, scheduler jobs, quota warmup claims, reset-credit redemption, rate-limit cooldowns, and per-account capacity accounting.

Affected components: DB migrations, account auth manager and refresh claims, scheduler leader election, durable bridge lease repository, ring membership, quota planner, reset credits, cache invalidation, and balancer cooldown state.

Why it recurs: process-local locks worked until multi-replica support arrived. SQLite and PostgreSQL also differ: `SELECT FOR UPDATE` is not a SQLite mutex, wall clocks skew, and local memory caches are not cross-replica truth. Correctness needs DB-clock leases, compare-and-set writes, durable claims, and post-lock rereads.

Evidence: `0a7f354d` serializes startup migrations; `b5f0541a` serializes single-use token refresh; `53f7b463` fences bridge lease writes and evicts fenced-out replicas; `a8e12f8e` makes warmup budget claims atomic; `991be817` persists cooldowns so peers honor them.

Machine-checkable invariant: every singleton action has a durable owner record with owner id, epoch, expiry on database time, and compare-and-set mutation; a stale owner can observe but cannot mutate current-owned state.

## Cache and quota freshness races

Mechanism: stale local quota/cache state overrides fresher durable evidence. Examples include quota-exceeded flags surviving reset evidence, runtime reset timestamps beating real usage rows, stale per-account model rejections pinning a route, and routing/selection/settings caches missing peer invalidations.

Affected components: usage refresh, load balancer selection, account cache, cache invalidation bus, quota planner, model routing, sticky selection, and dashboard-visible usage state.

Why it recurs: selection wants fast local reads, but quota truth changes asynchronously from upstream usage refreshes, reset windows, peer replicas, and manual account actions. The failure is usually not absence of data but wrong freshness precedence.

Evidence: `04d8fab8`, `dddd9615`, and `3b2fbd5` coalesce or decouple refresh from request selection; `7347745b`, `a269b376`, and `d739ebf1` repair reset/freshness precedence; `b7bf87cf` extends durable invalidation to routing and selection caches.

Machine-checkable invariant: every routing input carries a freshness source and version; a local snapshot may be used only if no newer durable invalidation or usage/reset version exists for the same account and quota window.

## Admission gate and lock contention

Mechanism: shared locks and gates both protect and create state-machine failures. Round-robin state mutated concurrently, bridge response-create gate contention used the wrong timeout and lost waiters, upstream close dropped admitted bridge waiters, stale gates were not replaced, and reject-fast admission turned temporary pressure into hard failure.

Affected components: balancer runtime locks, HTTP bridge response-create gate, admission queues, singleflight tests, account cap waits, HA/admission settings, and bridge upstream close handling.

Why it recurs: gates were added as local protections around one resource, then reused as global admission decisions. That creates lock-order and queue ownership questions: who owns a sleeping waiter, does it count against queue capacity, and what happens when the session closes while it sleeps?

Evidence: `7e5df879` serializes round-robin runtime state; `2b0d2fc1` reduces bridge/balancer lock contention after multiple review fixes; `87fae430` makes bridge gate contention wait-plan eligible; `fb5a573c` recovers stale response-create gates; `e5efbefe` stabilizes HTTP bridge singleflight tests.

Machine-checkable invariant: a gate waiter is either queued with a counted slot, actively holding the gate, or terminal; retries inherit the original deadline and cannot outlive the session or bypass the queue limit.

## Recurrence chains

- Reservation settlement: `592d47b3` fixed stream retry and compact errors, but `fe625bfb` later found a compact budget-exhausted preflight that still leaked, then `783665b9` fixed another header-failure release edge. The invariant must be global exactly-once settlement, not another local `finally`.
- Stream budget and stuck streams: `aa65e97d` decoupled stream duration from request budget, `77dbc8a` raised defaults, `de2c5fc0` extended WebSocket budgets, and `af5051f8` later found the pre-header no-byte wait. The model needs phase-specific timers, not one timeout knob.
- Continuity and stale anchors: `85802e64`, `4fccca1e`, `48f083ef`, `682447f2`, `4c04e538`, and `b1d27bc6` are a clear fix-of-fix chain. The model needs explicit anchor provenance and owner epoch rather than a boolean retry-safe flag.
- Bridge lease ownership: `53f7b463` establishes fenced ownership, while `93490404`, `9b40f746`, and `e6270f3f` cover idle release, startup purge, and owner-loss recovery. The model needs durable owner epochs and local-session eviction transitions.
- Cancellation/drain: `c9da4974`, `6e8fa56f`, `1089ab5d`, `66b9196d`, and `ec36ef60` show that terminal handling and shutdown are the same problem at different scopes: no producer may outlive its owner.

## Suggested formal model boundary

Start with a small TLA+/PlusCal or Python state-machine model of these entities: account lease, API-key reservation, bridge session, websocket turn, continuity anchor, owner epoch, gate waiter, and shutdown phase. Model only three replicas, two accounts, and two client turns; that is enough to expose stale owner writes, double release, lost waiters, and timeout ordering. Treat DB rows as the only durable truth and local caches as versioned snapshots.

The first model should not simulate token counts or full protocol payloads. It should model ownership, deadlines, terminal states, and freshness versions. Payload classes can be abstracted as `client_anchor`, `proxy_full_resend_anchor`, and `proxy_delta_anchor`.

## Top recurring classes

1. Stale continuity anchor and owner mapping - 12 rows - invariant: anchor use requires current owner epoch plus compatible lineage and safe payload provenance.
2. Timeout budget mismatch and stuck streams - 11 rows - invariant: each wait phase has a bounded deadline ordered under the original request deadline.
3. Lease and reservation leaks - 10 rows - invariant: every acquired capacity object settles exactly once on every terminal transition including cancellation.
4. Cache and quota freshness races - 9 rows - invariant: routing may use a local snapshot only if its durable freshness version is current.
5. Cross-replica single-owner coordination - 9 rows - invariant: singleton work mutates only under durable DB-clock owner claim and compare-and-set epoch.
6. Cancellation and terminal stream contamination - 8 rows - invariant: terminal/cancelled producers cannot enqueue into later requests and terminal reason is immutable.
7. Admission gate and lock contention - 8 rows - invariant: each waiter is counted queued active or terminal and inherits the original deadline.
8. Shutdown drain and background task lifecycle - 6 rows - invariant: draining forbids new work and all registered work reaches a bounded terminal state before owner release.
