## 1. Bounded teardown and reclaim

- [x] 1.1 Bound the shielded rollback/close teardown for file-backed SQLite sessions with a deadline derived from the busy timeout, preserving the shield against caller cancellation; PostgreSQL and in-memory SQLite (one shared StaticPool connection is the whole database — reclaim would destroy it, and it cannot starve other writers) keep the unbounded path
- [x] 1.2 On a missed deadline, interrupt the driver connection and invalidate it so the aiosqlite worker is disposed, the writer slot is released, and the connection can never be handed out again; report the reclaim with the long-write watchdog's identifiers (including ones already deferred into its pending report)
- [x] 1.3 Fence the wedged session against further teardown, consume the abandoned task's late failure, and close the session for bookkeeping once the abandoned teardown finishes; own the deferred close until completion and drain it at close_db so shutdown cannot abandon it

## 2. Tests

- [x] 2.1 A wedged sqlite rollback: close_session returns within the bound (fails on pre-fix unbounded teardown), the driver is interrupted, the connection invalidated, the reclaim log carries the watchdog identifiers, and an independent writer succeeds immediately while the wedge is still pending
- [x] 2.2 A wedged session is fenced from further teardown; the abandoned teardown finishing late is observed and followed by the bookkeeping close
- [x] 2.3 The bounded shield completes fast work, abandons at the deadline without cancelling, and absorbs caller cancellation like the unbounded shield
- [x] 2.4 Non-sqlite sessions keep the unbounded teardown: a slow rollback/close beyond the sqlite bound still runs to completion and is never reclaimed
- [x] 2.5 A wedged close without a transaction is bounded and fenced too
- [x] 2.6 In-memory SQLite keeps the unbounded teardown: a slow teardown is never reclaimed, the shared connection is never invalidated, and schema/data survive for later sessions
