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
