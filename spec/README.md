# codex-lb Core Ownership/Continuity/Timeout Model

This directory contains a TLC-checked TLA+ model of the core concurrency
protocol distilled from `spec/evidence/TAXONOMY.md` and
`spec/evidence/taxonomy.csv`.

The model boundary is intentionally small: two replicas, one served account,
and two client turns. Durable DB rows are the only truth; local caches are
modeled as versioned snapshots. Payloads and token counts are omitted.
Continuity anchors carry two things - a provenance class and the account that
owns them:

- `client_anchor`
- `proxy_full_resend_anchor`
- `proxy_delta_anchor`

The owning account is either the served account or the opaque identity
`foreign_account`, which stands for any account other than the one now serving
the turn. That single extra identity is enough to express cross-account anchor
injection without paying for a second full account dimension.

The turn lifecycle distinguishes three waiting phases, because live codex-lb
kills them under three different budgets:

- `queued` - waiting at the admission gate (`gateDeadline`).
- `active` - request dispatched upstream, `response.created` not seen yet.
  This is the **pre-response eventless phase** (`preResponseDeadline`).
- `streaming` - response started, incremental events flowing
  (`requestDeadline`, i.e. the stream-idle/request budget).

## Files

- `CoreOwnership.tla` - raw TLA+ model of account ownership, reservations,
  bridge/websocket turn lifecycle, anchors and anchor account ownership, owner
  epochs, gate waiters, snapshot freshness, completed-delivery finalizer
  ownership, shutdown drain, bounded deadline expiry with a separate
  pre-response eventless timer, and bounded client retry backoff.
- `CoreOwnership.cfg` - full model configuration. `check.sh` runs TLC without
  the `-deadlock` opt-out, so TLC deadlock checking stays enabled.
- `weak-*.cfg` - negative controls that set one weakening flag at a time.
- `check.sh` - verifies the pinned `tla2tools.jar` sha256, downloads through a
  temporary file before replacing the cache, runs the full model, then runs all
  weakenings and requires each one to fail with its mapped invariant name (or,
  for a liveness control, with its mapped temporal property and no invariant
  violation at all).
- `evidence/TAXONOMY.md` and `evidence/taxonomy.csv` - taxonomy inputs used to
  classify the historical bug exemplars.

## Checked Invariants

| Invariant | Requirement | Demonstrating action / weakening |
| --- | --- | --- |
| `Inv1AnchorCurrent` | Anchor use requires current owner epoch, compatible lineage, and safe provenance. | `UseAnchor` records `badAnchorUse`; `weak-ignore-owner-epoch.cfg` demonstrates stale anchor reuse can violate it. |
| `Inv2DeadlineOrdering` | Connect, first-byte, gate, and request deadlines remain ordered under the original request deadline. | `QueueTurn` assigns phase deadlines; `weak-single-shared-timeout.cfg` demonstrates a shared timeout can violate ordering. |
| `Inv3ReservationSettledExactlyOnce` | Every acquired terminal turn has exactly one settlement event and a settled reservation state. | `CompleteTurn`, `CancelTurn`, `ExpireDeadline`, and `FinalizeCompletedDelivery` increment `settlementCount`; `weak-skip-release-on-cancel.cfg`, `weak-double-settle.cfg`, and `weak-popped-not-finalized.cfg` demonstrate zero, double, and lost-finalizer failures. |
| `Inv4FreshSnapshots` | Routing cannot use a local snapshot behind durable freshness evidence. | Normal `RouteFromSnapshot` consumes a fresh snapshot; `weak-stale-cache.cfg` removes the freshness guard. |
| `Inv5SingleOwnerCAS` | Singleton/account work mutates under a single durable owner epoch. | `AcquireTurn` enforces empty durable ownership and no live turn on the same account; `weak-non-atomic-claim.cfg` demonstrates duplicate live owners. |
| `Inv6TerminalIsolation` | Terminal or cancelled producers cannot enqueue into later turns, and terminal reason is present. | `MisrouteProducer` is disabled in the full model; `weak-lost-waiter.cfg` demonstrates terminal producer contamination. |
| `Inv7GateAccounting` | Every gate waiter is exactly queued, holding, or terminal and keeps the inherited deadline. | `QueueTurn`, `AcquireTurn`, and terminal actions preserve the gate lattice; `weak-lost-waiter.cfg` demonstrates a dropped queued waiter. |
| `Inv8ShutdownDrain` | Draining forbids admission of new externally visible work and shutdown completion requires no registered work. | `QueueTurn` is gated by `CanAdmit`; `weak-shutdown-admit.cfg` demonstrates post-drain admission. |
| `Inv9TerminalOwnerReleased` | Every acquired terminal turn releases the durable owner slot and finalizer owner. | Terminal actions call epoch-fenced release; `weak-leak-owner-on-terminal.cfg` demonstrates a leaked owner lease. |
| `Inv10AnchorAccountOwnership` | A request never dispatches with a continuity anchor owned by a different account, and no turn past admission carries a foreign-owned anchor. | `AcquireTurn` refuses a foreign-owned injected anchor (the injection is weakening-only, like `MisrouteProducer`) and records `crossAccountDispatch`; same-account anchors still come from `StartStream` and are shown usable by `UseAnchor`, so the guard is not vacuous. `UpstreamRespondsTo` makes `response.created`/`response.completed` unreachable for a foreign anchor, so the weakened turn wedges in the pre-response phase. `weak-cross-account-anchor.cfg` demonstrates it. |
| `Inv11PreResponseBudget` | The pre-response eventless bound is derived from the named budgets - it is the minimum of the owner-side gate-retire and stream-idle budgets, at or above the keepalive cadence floor, and strictly below the post-start stream-idle budget - and no kill is ever reported under the post-start stream-idle budget while the response had not started. | `QueueTurn` assigns `preResponseDeadline`/`gateRetireDeadline` from named budgets; `ExpireDeadline` picks its bound and its budget label per phase and records `mislabeledKill`; `weak-conflated-timers.cfg` demonstrates a single shared timer name killing a healthy pre-start wait under the wrong budget. |

The full configuration also checks natural liveness properties:

- `TurnTermination`: every admitted turn eventually reaches a terminal state.
  This is derived from bounded `Tick`, `ExpireDeadline`, and
  `FinalizeCompletedDelivery` fairness, not from fairness on cancellation.
- `ShutdownEventuallyComplete`: committed shutdown drain eventually completes
  once registered work has left.
- `TearEventuallyRecovers`: a recoverable tear (a turn killed in the
  pre-response eventless phase) is eventually recovered by the client. This
  holds only under the bounded-delay assumption on `retryBackoff`: while the
  backoff stays inside `MaxRetryBackoff` a retry is always eventually due, so
  `WF_vars(ClientRetryAttempt)` can discharge it.

## Negative Controls

Each weakening is a TLC constant value for `Weakening`. The full model uses
`Weakening = "none"`. `check.sh` treats a passing weakening, a missing
counterexample trace, or a counterexample against the wrong invariant as a
failure. The liveness control additionally fails if it violates any invariant,
or if its config does not declare exactly the mapped temporal property.

| Config | Disabled guard | Expected violation | Taxonomy class | Exemplar SHAs / live evidence |
| --- | --- | --- | --- | --- |
| `weak-ignore-owner-epoch.cfg` | Allows continuity anchor reuse without current owner epoch/provenance fencing. | `Inv1AnchorCurrent` | Stale continuity anchor and owner mapping | `85802e64`, `48f083ef`, `4c04e538`, `b1d27bc6` |
| `weak-single-shared-timeout.cfg` | Collapses phase-specific deadlines into a single mismatched timeout. | `Inv2DeadlineOrdering` | Timeout budget mismatch and stuck streams | `aa65e97d`, `de2c5fc0`, `af5051f8` |
| `weak-skip-release-on-cancel.cfg` | Lets cancellation bypass reservation release/finalization. | `Inv3ReservationSettledExactlyOnce` | Lease and reservation leaks | `592d47b3`, `015f669e`, `783665b9` |
| `weak-double-settle.cfg` | Allows a terminal acquired turn to settle twice. | `Inv3ReservationSettledExactlyOnce` | Duplicate finalization and replayed settlement | `592d47b3`, `015f669e`, `783665b9` |
| `weak-stale-cache.cfg` | Lets a replica route from a local snapshot behind durable invalidation. | `Inv4FreshSnapshots` | Cache and quota freshness races | `04d8fab8`, `7347745b`, `b7bf87cf` |
| `weak-non-atomic-claim.cfg` | Lets a second turn claim an account without durable compare-and-set exclusion. | `Inv5SingleOwnerCAS` | Cross-replica single-owner coordination | `0a7f354d`, `b5f0541a`, `53f7b463`, `a8e12f8` |
| `weak-lost-waiter.cfg` | Lets cancellation drop a queued waiter slot or misroute a terminal producer. | `Inv6TerminalIsolation` or `Inv7GateAccounting` | Admission gate and lock contention; cancellation contamination | `87fae430`, `03b77781`, `c9da4974` |
| `weak-shutdown-admit.cfg` | Allows new externally visible turns after drain starts. | `Inv8ShutdownDrain` | Shutdown drain and background task lifecycle | `66b9196d`, `ec36ef60`, `3bdc9dea` |
| `weak-leak-owner-on-terminal.cfg` | Lets terminal completion/cancel/timeout leave the durable owner slot assigned. | `Inv9TerminalOwnerReleased` | Lease and reservation leaks | `592d47b3`, `015f669e`, `783665b9` |
| `weak-popped-not-finalized.cfg` | Models `response.completed` being popped from pending before the finalizer owns cleanup, then aborting. | `Inv3ReservationSettledExactlyOnce` | HTTP bridge completed-event cleanup ownership loss | `1594`, `778c533f`, `592d47b3` |
| `weak-cross-account-anchor.cfg` | Lets `AcquireTurn` dispatch a request carrying an injected anchor owned by another account. | `Inv10AnchorAccountOwnership` | Cross-account continuity anchor wedge | PR `1638`, incident `2026-08-06` |
| `weak-conflated-timers.cfg` | Collapses the pre-response eventless watchdog and the post-start stream-idle watchdog into one budget name. | `Inv11PreResponseBudget` | Pre-response eventless timeout misclassified as stream idle | PR `1633`, incident `2026-08-06` |
| `weak-unbounded-backoff.cfg` | Lets the client retry backoff grow past every deadline in the model, so no reconnect is ever due. | property `TearEventuallyRecovers` | Client reconnect loop that never recovers | PR `1634`, incident `2026-08-06` |

## Live Evidence Mapping, 2026-08-06

The three newest controls come from the 2026-08-06 keepalive-window incident on
the live blue-green stack rather than from historical commits, so their
exemplar column carries PR numbers and the incident date instead of fix SHAs.

- **`weak-cross-account-anchor.cfg` -> `Inv10AnchorAccountOwnership`.** The
  incident root cause was an injected `previous_response_id` owned by a
  *different* account. Upstream accepts such a request and then never emits
  `response.created`, so the turn sits in the pre-response eventless phase
  until a timer kills it and the client re-presents the same dead anchor. That
  is exactly what the model reproduces: with the guard removed, `AcquireTurn`
  sets `crossAccountDispatch`, and `UpstreamRespondsTo` makes `StartStream`,
  `CompleteTurn`, and `ClaimCompletedDelivery` unreachable for that turn.
  Fixed live as PR `1638`, *never inject a cross-account previous_response_id
  anchor*. Session-level evidence for the resulting dead-anchor loops is in
  `memory/codex-lb-formal/incidents/2026-08-06-keepalive-window/SESSIONS_TRACE.md`
  (sessions `a0eb3b03df19` and `33755fa72727`, durable rows `60732a9c...` and
  `8e04f345...`, retry circuits at 10 and 7 consecutive `stream_idle_timeout`
  failures). The `bridge_reconnect_account_swap` torn state proven in
  `memory/codex-lb-formal/EVENT_BUGS.md` is the same account-identity hazard
  seen from the reconnect side.
- **`weak-conflated-timers.cfg` -> `Inv11PreResponseBudget`.** The audit in
  `memory/codex-lb-formal/MISCLASS.md` proved the `keepalive_count=6
  max_keepalive_count=6` kill is a *pre-response-start* event-queue silence
  timer worth `6 * 10s = 60s`, not the configured `stream_idle_timeout_seconds
  = 7200s` it was reported as, and that it sat below the `300s` owner-side
  missing-created gate instead of being related to it. The saved incident
  corpus has `788` `HTTP bridge stream idle timeout` lines and `57/64` reader
  failures with `response_events_seen=0`, while healthy `gpt-5.6-luna` turns
  had a first-upstream-event p95 of `930 ms` - so the label was wrong, not the
  traffic. Fixed as PR `1633`, *name the pre-response eventless timeout
  honestly and derive its budget from settings*; the model encodes the shipped
  derivation `min(gate_retire, stream_idle)` floored by the keepalive cadence.
  The weakening restores the single shared name and TLC produces a healthy
  pre-start wait killed under the `stream_idle` budget.
- **`weak-unbounded-backoff.cfg` -> `TearEventuallyRecovers`.** Both wedged
  interactive sessions in `SESSIONS_TRACE.md` looped half-open -> circuit open
  -> reattach for hours with growing cooldowns, and the client side answered
  with ever longer sleeps - the observed extreme being a 29-hour client retry
  gap. Cooldown expiry is a retry gate, not a dead-anchor cleanup, so nothing
  in that loop bounded the delay. The model states the bounded-delay assumption
  explicitly (`ClientRetryAttempt` requires `retryBackoff <= MaxRetryBackoff`)
  and the weakening lets `GrowRetryBackoff` push the delay past it forever;
  TLC then produces a behaviour where `clientRetry = "torn"` never becomes
  `"recovered"`. PR `1634`, *complete Codex compaction SSE lifecycle*, is the
  live counterpart on the recovery path: a thread-recovery stream that
  completed an output item for a response that was never created left stateful
  clients waiting and retrying instead of finishing. The unbounded
  `event_queue.get()` proven in `memory/codex-lb-formal/EVENT_BUGS.md`
  (candidate 1, reachable when `sse_keepalive_interval_seconds <= 0`) is the
  server-side twin of the same "no bound, no recovery" shape, and is why
  `Inv11PreResponseBudget` requires the pre-response bound to exist and stay at
  or above the keepalive cadence floor.

The `2026-08-06` incident also proved dead durable anchors surviving process
kills (`docker stop` grace `10s` against a committed `30s + 25s` app drain, so
`app/main.py` bridge cleanup never ran) and a same-container restart reusing
the bridge instance id, so the startup purge did not retire the previous
process's rows. That is a deploy-side inequality and a durable-identity gap
rather than a new core-protocol invariant; it is tracked in
`memory/codex-lb-formal/incidents/2026-08-06-keepalive-window/INCIDENT.md` and
listed under Future Work below.

## Conformance Gap Modeled

The model includes `completed_delivery_claimed`, `poppedFromPending`,
`completedDeliveryClaimed`, `finalizerOwner`, and `finalizerAborted`. The full
model requires the claimed completed delivery to be finalized before becoming
terminal; `weak-popped-not-finalized.cfg` permits the abort transition that the
conformance review confirmed from the implementation and proves it violates
`Inv3ReservationSettledExactlyOnce`.

## Running

```sh
bash spec/check.sh
```

Expected result: the full model completes with zero violations and deadlock
checking enabled; all weakening runs fail with their mapped invariant names,
and the liveness control fails with its mapped temporal property while keeping
every invariant. The script prints the distinct-state count for the full model
and every weakening so the checked state-space size is visible.

## Future Work

The conformance review also recommends modeling separate account reservations,
stream leases, durable bridge claim/renew/release states, client-visible
delivery state, excluded-account failover, stale reservation repair, and
reversible operator drain distinct from committed shutdown. The 2026-08-06
incident adds two more: a process/boot epoch in durable bridge session
ownership so a same-container restart cannot inherit the dead process's rows,
and the deploy-side inequality `stop grace >= committed drain + post-drain
cleanup reserve`. Those dimensions remain outside this tractable core model.
