## Context

The application drains requests and marks bridge membership stale before final
resource teardown. Managed HTTP clients own their close tasks, and their global
close function waits for completion events without a deadline. If one event
never arrives, later process-death marking and database disposal never execute.

## Decisions

Run the existing global HTTP close in a named task. Retain it in a process-local
set until completion, and use `asyncio.wait` with a 10-second bound. A timeout
abandons only the wait; it does not cancel underlying client-owned cleanup.
The done callback releases the reference and consumes a terminal exception.
Self-cancellation is logged without failing the later shutdown stages. Caller
cancellation retains its existing propagation semantics.

Keep the current shutdown ordering and all other budgets unchanged. No new
setting or dependency is needed for this missing bound.

## Verification

Use the real application lifespan with its external dependencies stubbed. A
blocked HTTP close must not prevent process-death marking or database disposal.
The same test on the current baseline must fail. Focused helper cases cover
prompt completion, timeout ownership, immediate and late errors, and
self-cancellation. Existing lifecycle and database clean-marker tests must pass.

## Non-goals

No scheduler-task grace changes, forced transport termination, new global
shutdown deadline, or native-egress close changes.
