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

The reservation boundary is modelled the way production has it, because the
first version of the checker counted release *calls* and its 200-seed green
turned out to be a timing coincidence: with only the modelled write latency
changed (0.03 instead of 0.01, or the reservation write merely slower than the
account-lease release) 25-40% of the seeds failed on pristine production with
two or three `_release_websocket_reservation` calls per turn, e.g.
`('abort', 'detach')`, `('abort', 'detach', 'detach')` and, with no
cancellation at all, `('finalize', 'detach')`. Production tolerates those
calls through the `status == "reserved"` compare-and-set in
`ApiKeysService._release_usage_reservation_once` /
`settle_usage_reservation`, not through exactly-once calls: the detach
backstop decides `detached` under `pending_lock`, awaits the gate release and
only then reads `request_state.api_key_reservation`, which the terminal path
(draining-branch finalizer or shielded abort settlement) may already be
writing, and every path clears the reservation only after its awaited write
returns. The recording service therefore models the compare-and-set per
reservation id (`_write_reservation`: the first completed write is the
settlement, later ones are recorded as redundant), runs the finalizer's
settlement as a detached scheduler-owned write that survives the caller's
cancellation like `_settle_stream_api_key_usage` (including the shielded
wait loop for ordering-sensitive callers), keeps
`_release_websocket_reservation` interruptible in the calling task (a raw
`Task.cancel()` cuts through production's anyio shield there too), and gives
the load balancer's success write a round trip so the finalizer suspends after
the settlement transfer. "Exactly once" is one effective flip; the invariant
is checked under four write-latency profiles. The redundant calls are a
production observation, not a harness one, so they are pinned by the strict
known-failing `test_bridge_turn_lifecycle_settles_reservation_with_a_single_write`
(8 of 200 default-profile seeds, 60 under uniform 30 ms, 46 with the
reservation write slower, none with the account-lease release slower) rather
than asserted away or folded into the invariant.

The settlement cancel lands at a seeded virtual offset after the claim
(`_SETTLEMENT_CANCEL_OFFSETS`) in addition to the seeded loop turns: loop turns
alone always fired before the first modelled write completed, so the cancel
never reached the reservation write or the window after the settlement
transfer and the post-settlement claim guards were untested. Each landing is
classified from product state (`before_settlement`,
`inside_reservation_write`, `after_settlement`; default profile: 34/33/18 of
200 seeds, 18 interrupted writes) and the coverage test asserts all three. The
`abort_after_transfer` invariant is what makes the post-settlement window
observable under the compare-and-set model: once the finalizer settled the
reservation, the shielded abort path must find the claim cleared and never
write that reservation. Pristine production satisfies it on every profile and
on the first 3000 default-profile seeds.

The canaries plant production-shaped bugs in overridable seams: a detach that
releases the response-create admission it does not own, an abort path that
never settles a claimed request (the same failure a never-recorded claim
produces), a reservation release that silently does nothing, a post-settlement
retry that reacquires ownership, and a lease release that re-shields a pending
task. Each fails the checker at a deterministic seed with the invariant it
violates. Run against production mutants, the checker catches a dropped
terminal claim, an abort path that skips settlement, a double admission release
in `_release_websocket_response_create_gate`, that helper awaiting the account
lease release without the deferring wrapper, a re-introduced
`asyncio.shield` in `_await_task_deferring_cancellation` (on CPython 3.14,
where the shield residue exists), and, through `abort_after_transfer`, the
compound post-settlement guard removals: the finalizer clearing neither the
claim marker nor the reservation after the settlement transfer, and the abort
path ignoring the claim marker while the finalizer leaves the reservation set.
Removing a single post-settlement guard (only the marker clear, only the
reservation clear, only the abort path's marker check, only the draining
branch's marker clear) is an equivalent mutant: each guard is redundant with
its partner by design, so no oracle can see one alone. Under the
compare-and-set model a mutant whose only effect is an extra release call
on an already settled reservation is not a violation; it shows up in the
snapshot's `redundant_reservation_releases`. Mutants it cannot see,
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
stale waiter entry, and every later `acquire()` parks forever
(`Lock.release` skips a cancelled waiter but leaves its entry queued; an
already-runnable acquirer sees the non-empty queue, takes the slow path and
parks; the cancelled waiter's cleanup finds no owner to hand over from). The
shape is production's own: `_await_cancelled_task` raw-cancels the bridge
reader while it sits on `session.pending_lock`. anyio 4.14.0 fixed it
upstream ("Fixed asyncio Lock and Semaphore deadlocks caused by cancelled
waiters left queued during release", agronholm/anyio#1145); the dependency
bump is a separate decision. It is pinned rather than described: a minimal
stdlib+anyio reproduction (`tests/simulation/test_anyio_lock_cancelled_waiter.py`)
and the production-turn seeds that wedge `pending_lock` under the default
profile in the first 3000 (`_ANYIO_LOCK_WEDGE_SEEDS`, currently seed 1234;
the default 200-seed run and the other latency profiles' 200 seeds are free
of wedges) are strict expected failures conditioned on `anyio < 4.14`, so a
widened schedule count or a dependency bump reports the change instead of
looking like harness rot. Seed 1234 passes the full invariant set with the
4.14 `Lock.release` fix applied.

The abandoned-shield oracle (`max_abandoned_shield_callbacks`) counts
`_clear_awaited_by_callback` entries, which `asyncio.shield` registers only
from CPython 3.14; on 3.13 the outer's cancellation removes the single
callback again and there is no residue. CI's unit slice runs on 3.13 while the
production image is 3.14, so the oracle's count assertions and the reshield
canary are gated on `ABANDONED_SHIELD_ORACLE_SUPPORTED` (with the 3.13
reading pinned to zero so the platform assumption is checked both ways) and
the `abandoned_shields` invariant is documented as vacuous on 3.13. Running
the simulation slice on 3.14 in CI, matching the image, is the owner's call.

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
`select_account` calls main left on the selector's internal `time.time()`
default (`_select_account_preferring_budget_safe` and the direct calls inside
`_select_with_stickiness`) keep that default: production-identical under
`RealClock`, but a virtual-clock selection test cannot move those cooldown and
reset comparisons yet. Threading `now=` through them is the next fidelity
step, kept out of this change so the sticky diff against main stays a pure
`time.time()` -> `clock.time()` substitution at the same sites. The
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
