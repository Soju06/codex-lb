# Context

This change is deliberately a first harness, not a complete conversion of every
proxy timing seam. The seam audit found hundreds of direct time reads and more
than one hundred sleeps or waits. Rewriting all of them at once would make the
review about incidental plumbing instead of proving one deterministic lifecycle
slice.

The production adapters default to real `asyncio` and real time. Tests opt into
virtual time by constructing services or controllers with explicit clock and
scheduler objects. The virtual scheduler runs on the pytest loop but owns its
timers and task registry, so tests can advance deadlines without wall-clock
sleep and can cancel owned tasks at the reset boundary.

Inside the proxy mixins the seam is read through `scheduler_for` and `clock_for`
rather than a bare attribute. Bridge and websocket lifecycle methods are also
called with partial service doubles, so requiring every caller to carry a
collaborator would turn a test-only construction detail into a runtime failure.
The accessors fall back to the real scheduler and real clock, which is the same
production default the constructors use.

The audit's third injection item, an upstream transport factory, is deliberately
left out. The converted tests reach their upstream through the existing fake SSE
and fake websocket doubles, so a transport factory would be new production
surface with no consumer yet. It stays available for the next slice.

The first schedule checker models the recurring lease and terminal-state bug
class from the taxonomy: a bridge turn must reach exactly one terminal outcome
and release response-create, API-key, and account leases exactly once even when
admission, upstream terminal delivery, downstream cancellation, a cancellation
landing inside the settlement itself, and retry attachment interleave.

The checker drives production code rather than a model of it. The terminal
event runs `ProxyService._process_http_bridge_upstream_text` on a real
`_HTTPBridgeSession` (production claim under `pending_lock`, publication,
`_finalize_websocket_request_state`, `_release_websocket_response_create_gate`),
the downstream cancel runs the detach backstop `_detach_http_bridge_request`,
the retry runs `_retry_http_bridge_precreated_request`, and the admission wait
contends for a permit on a real `WorkAdmissionController`. The recording service
overrides only the repository boundaries (reservation release/settlement, the
load balancer's health write, reconnect) and tags every reservation settlement
with the production path that performed it, so "exactly one terminal outcome"
means "exactly one production path settled the reservation". Every event in a
schedule is a concurrent task with its own seeded virtual deadline, so equal
deadlines produce real interleaving at the await points inside those paths.

The terminal frame is a `response.completed` without a response id on purpose:
the anonymous-match claim leaves the request retryable-looking (no response id,
still awaiting `response.created`) until finalization, which is the window the
retry ownership guard protects, and it is the only terminal shape that reaches
settlement without routing a pre-created request into the in-path precreated
retry (`error`/`response.failed` do) or bumping `response_event_count` at claim
time. The settlement cancellation is a real `Task.cancel()` delivered a seeded
number of loop turns after the production claim, once per bookkeeping task:
`_await_cancelled_task` cancels a child once and re-tracks a stubborn one with
`cancel_task=False`, and anyio's shield does not stop a raw asyncio cancel, so a
second cancellation into the shielded abort path is outside the modelled
contract. Modelled lease releases suspend on a virtual timer, standing in for
the DB writes they replace, so cancellations can land inside them.

The canaries plant production-shaped bugs in overridable seams: a detach that
releases the response-create admission it does not own, an abort path that
never settles a claimed request (the same failure a never-recorded claim
produces), a reservation release that silently does nothing, a post-settlement
retry that reacquires ownership, and a lease release that re-shields a pending
task. Each fails the checker at a deterministic seed with the invariant it
violates. Run against production mutants, the checker catches a dropped
terminal claim, an abort path that skips settlement, a double admission release
in `_release_websocket_response_create_gate`, that helper awaiting the account
lease release without the deferring wrapper, and a re-introduced
`asyncio.shield` in `_await_task_deferring_cancellation`. Mutants it cannot see,
by construction of this slice: a checkpoint in the websocket-transport cleanup
helper (`_release_websocket_response_create_ownership_for_cleanup` is not on
the bridge path), a checkpoint before `_release_websocket_reservation` in
`_release_websocket_request_state_reservation` (the bridge finalizer settles
through `_settle_stream_api_key_usage`; the helper is reached only by the
detach, which is never cancelled, and by the abort path, after the cancellation
was consumed), the detach's belt-and-braces reclaim of an `abandoned` claim
(only reachable when the abort settlement itself raises), and the retry
ownership guard (`request_is_retryable` masks it on every terminal shape; the
guard is load-bearing for the stale-gate-holder and reader-failure funnels,
the natural next slice).

Known finding from the harness, out of scope for this change: with the pinned
anyio 4.13.0, a raw cancellation of a task parked on an `anyio.Lock` that races
the holder's `release()` in the same loop tick leaves the lock unowned with a
stale waiter entry, and every later `acquire()` parks forever. Seven of the
first 3000 production-turn seeds (340, 1079, 1590, 2010, 2331, 2589, 2744)
wedge `pending_lock` this way after the injected reader cancellation; the
default 200-seed run does not. anyio 4.14.0 fixed it upstream
("Fixed asyncio Lock and Semaphore deadlocks caused by cancelled waiters left
queued during release", #1145); the dependency bump is a separate decision.

## Takeover notes (PR A)

`Scheduler.wait` exists so the proxy's timed `asyncio.wait(fs, timeout=...)`
sites can be injected without rewriting them as
`scheduler.wait_for(asyncio.wait(fs), timeout)`. That rewrite changed
semantics (a task completing in the deadline tick was reported as a timeout
instead of done) and produced dead `if not done:` branches and "recheck"
hacks. With `wait`, every such site keeps main's control flow character for
character apart from the `asyncio.` -> `scheduler.` prefix.

`Scheduler.fail_after` exists for the same reason: main already bounds the
whole account-selection block with `anyio.fail_after(remaining_budget)`.
Injecting the scope gives the harness the same deadline with zero production
change, instead of adding a second scheduler-owned timeout inside it.

`RealScheduler` owns nothing. A registry of every rerouted task would extend
task lifetimes in production and let `cancel_owned_tasks()` on the
process-wide singleton cancel live work; ownership tracking belongs to the
virtual scheduler only. `tests/unit/test_clock_real_parity.py` pins that each
real adapter behaves exactly like the primitive it wraps.

Budget math is a clock-domain concern: a deadline computed from the injected
clock must be compared against that clock. `ProxyService._remaining_budget_seconds`
(from `_service/clock_budget.py`) is the reader on the turn path; the
module-level function of the same name stays wall-clock for the endpoints
outside the simulation scope (compact, codex_control, file_ops, transcribe).

The virtual `wait_for` and `fail_after` keep the *shape* of the primitives
they replace, not just their timing (pinned by
`tests/simulation/test_virtual_time.py`): `wait_for` awaits the coroutine
inline in the calling task under an `asyncio.timeout`-style virtual deadline
(same `current_task()`, contextvar writes visible to the caller, task-bound
primitives such as `anyio.Lock` usable across the call, a plain awaited future
cancelled on expiry, an inner `anyio.CancelScope(shield=True)` cut through
exactly like the real primitive), and `fail_after` enters a real
`anyio.CancelScope` that the virtual deadline cancels, so anyio itself
delivers the cancellation (inner shields finish first, re-delivery at every
checkpoint, a racing external cancellation is kept). The one intentional
difference from a wall clock is *when* the deadline fires; a same-tick tie
between an awaitable and its deadline reports `TimeoutError`, as CPython does
when its timer callback runs before the task's wakeup.

## Integration notes (WP2-WP4)

Shared support helpers take the caller's clock sample as a required parameter
(`now=` / `clock=`) rather than a `REAL_CLOCK` default idiom: every caller is
in-repo, and a default would let a future caller silently compare an
injected-clock timestamp against the wall clock again. The same rule makes
`_sleep_for_account_selection_recovery` and `_wait_for_websocket_continuity_gap`
require their `scheduler`/`clock`.

Known limitations of this slice, all left raw on purpose and carried in the
timing-seam allowance table: the durable operation-event batcher lazily starts
a wall-clock flusher task that no scheduler owns (a durable-id session under
`VirtualScheduler` would leak it; the property turn's fake session has no
durable id), the realtime relay and the compact endpoint keep their own
transport loops, the request-log shutdown drain stays on `loop.time()`, and
the process-global TTL caches (stale previous-response, upstream transport
failure, account and rate-limit caches) read the wall clock by design. The
property turn stubs `_release_websocket_reservation`,
`_settle_stream_api_key_usage`, `_reconnect_http_bridge_session`,
`_release_retry_account_lease`, `_get_work_admission` and the load balancer's
`record_success`, so ownership of the api-key settlement/heartbeat, reconnect,
request-log and operation-event spawns is proven by the recording-scheduler
unit tests rather than by the 200-seed run.

Spawn channels that used to bypass the scheduler on the turn path now go
through it: the three `asyncio.shield(<coroutine>)` sites in the reconnect
abort path shield a scheduler-created task instead (`shield` would
`ensure_future` an unowned one), the grouped-terminal persistence `gather`
receives scheduler-created tasks, and
`_await_result_deferring_cancellation` / `_await_cleanup_deferring_cancellation`
take a `scheduler` (real default) and spawn a coroutine through it, which
`_release_websocket_response_create_gate` requires from every caller. All of
these are `asyncio.create_task` under `RealScheduler`, so production is
unchanged. Allowance: the route-level cleanups in `app/modules/proxy/api.py`
call the deferring-cancellation helpers with the real default; the route layer
is outside the simulated turn, and the guard PR should list
`app/core/utils/shared_future.py` (its `ensure_future` fallback serves
non-coroutine awaitables) alongside the proxy modules it scans.
