## Context

See proposal.md. The diagnosis came from two independent investigations (code
reading, and an instrumented reproduction), a verifier using a different
attribution method (stamping the current task/coroutine onto `sqlalchemy.pool`
log records), and a separate re-measurement. Details are in issue #2505.

## Goals / Non-Goals

**Goals:** stop cancelling DB-owning background tasks mid-transaction, release
the leader lease on prompt shutdown, and keep shutdown bounded.

**Non-Goals:**
- Changing the shutdown order in `app/main.py`.
- Making the stops concurrent.
- Adding a setting for the grace.
- Changing Postgres pool behavior.

## Decisions

**Signal, bounded wait, then cancel.** The loops already wait with
`asyncio.wait_for(stop.wait(), timeout=interval)`, so after the stop signal
they exit right after the current tick. Cancelling immediately only ever hurt
the one case that matters: a task in the middle of DB work. A shared helper
(`stop_task_after_grace`) keeps the behavior identical across the stop paths.
*Alternative:* shield the DB unit of work from cancellation inside each loop.
Rejected as more invasive, and it still needs a bound.

**A fixed 2s grace.** Measured post-fix shutdowns completed in a median of
0.56s and a max of 0.82s. 2s leaves margin for a slow transaction without a new
`CODEX_LB_*` knob (the settings budget is at its ceiling).

**Sequential stops kept.** The existing order in `app/main.py` is deliberate
(e.g. cache invalidation stops after the model scheduler). Running the stops
concurrently would cap the worst case at about 2s, but it changes an ordering
contract this fix doesn't need to touch.

**WARNING on fallback cancel.** A task that needed cancelling is exactly the
case operators should see. It names the coroutine.

**Maintainer review: two stop policies, and one loop turn first.** The
drain-deadline cap is right for the periodic schedulers, but wrong for the
lease keeper. The keeper is stopped inside `release()`, which already runs under
the 10s `_release_leader_lease_within` deadline, and `release()` relies on the
cancelled keeper having *finished* (one renewal owner at a time). On a busy
instance the drain deadline is exhausted at shutdown (streaming responses open
at SIGTERM), so a zero-budget keeper stop returned while the renew session was
still unwinding. So:
- the keeper uses the plain grace and awaits the cancelled task
  (`await_cancellation=True`);
- the schedulers keep the drain-capped grace but wait up to the plain grace
  after cancelling, so a promptly cancelled task is finished before the next
  stop (preserving "poller after model scheduler");
- every stop first yields one loop turn, so a loop idling on its stop event
  exits without a spurious WARNING.

## Risks / Trade-offs

- [Worst case: several tasks mid-tick in slow DB work] → Uncapped, each stop
  could take its grace plus the bounded post-cancel wait (2s + 2s), so seven
  sequential stops could reach about 28s. That exceeds the server's 25s
  post-drain cleanup reserve, after which it force-exits before the lease
  release and DB disposal (found in review). Each wait is therefore capped by
  the time left in the shared *drain* deadline
  (`shutdown_state.remaining_drain_timeout_seconds()`), never touching the 25s
  post-drain cleanup reserve. That reserve is exactly the lease-release
  deadline (10s) plus the metrics-server wait (5s) plus `close_db()`'s bounded
  teardown drain (2 x 5s). A first revision subtracted a 15s reserve from the
  post-drain budget, which left `close_db()` unbudgeted (also found in review).
  With no drain time left, a task is cancelled without grace, and one that
  still defers cancellation is tracked, so the clean record is withheld.
- [PostgreSQL untested] → The code path is backend-independent, but neither the
  symptom nor the fix was measured on Postgres. This is called out in the
  proposal and the PR.
