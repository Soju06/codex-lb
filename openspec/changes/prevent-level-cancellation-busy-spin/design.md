## Context

The original implementation preceded production hotfix #1969 and consolidation #1992. Current `main` now owns cancellation-deferring behavior in `app/core/utils/shared_future.py`: `wait_on_shared_future()` avoids per-waiter callback churn, while `_await_task_deferring_cancellation()` combines that wait with an AnyIO shield and `checkpoint_if_cancelled()`.

Two residual HTTP bridge paths still violate cleanup ownership. Terminal append/delivery barriers set boolean release flags before awaited callbacks finish, so cancellation can make later cleanup believe an incomplete barrier was released. Session close awaits resource cleanup safely, but then removes detached capacity inline; cancellation while that final registry-lock acquisition waits can leave a closed generation permanently tracked.

## Goals / Non-Goals

**Goals:**

- Make grouped terminal barriers re-awaitable and exactly once.
- Keep terminal delivery and barrier ordering ahead of cancellation propagation.
- Keep detached bridge capacity tracked until registry finalization actually completes.
- Prevent any cancellation-catching loop from repeatedly attaching `asyncio.shield()` callbacks.
- Reuse the canonical shared-future primitive from current `main`.

**Non-Goals:**

- Replace or modify `wait_on_shared_future()`.
- Rework API-key, Compact, database teardown, SSE, streaming retry, or HTTP bridge helper-body waits already handled by #1969/#1992.
- Change bridge-ring heartbeat scheduling, transcript retention, external responses, settings, or schema.

## Decisions

### D1. Rebuild only the residual delta

The two original commits mixed code now absorbed by `main` with still-needed ownership fixes and the structural guard. The branch is rebuilt from current `main` rather than replaying obsolete implementations. A backup ref retains the reviewed original head, and range-diff plus focused tests account for the intentional scope reduction.

### D2. Represent each terminal barrier with one lazy owned task

Each append or delivery barrier callback is wrapped in a task on first use. Every release attempt awaits that same task through `_await_task_deferring_cancellation()`. A cancellation marker is accumulated and propagated only after append completion, terminal enqueue, delivery-barrier completion, and required settlement. The append owner captures an ordinary optional-spool failure as result data so the canonical helper can return a simultaneously deferred caller-cancellation marker; the outer error path then performs owned fallback delivery and re-raises the cancellation. A failed optional spool therefore cannot erase cancellation.

This replaces pre-await boolean flags. Task identity is the exactly-once marker: a pending task can be re-awaited, and a completed task cannot invoke its callback again.

### D3. Finalize detached capacity through an independent owner

After the resource-close owner settles, session close creates one task to remove that exact detached session under the registry lock. The canonical defer helper keeps the task alive if cancellation arrives while the lock is held. The stored cancellation marker is raised only after finalization completes. Identity checks remain unchanged so stale cleanup cannot remove another generation.

### D4. Reject repeated `asyncio.shield()` attachment structurally

The checker finds loops whose `try` body awaits a direct, aliased, or assigned `asyncio.shield()` and whose handler catches cancellation without terminating the loop. Bare handlers and `BaseException` handlers count because they catch `CancelledError`.

An outer `anyio.CancelScope(shield=True)` is not an exemption. It blocks level cancellation, but direct cancellation of the waiter can still re-enter the loop, and Python 3.14 can leave one callback on the owned task per shield attempt. Safe code uses `wait_on_shared_future()` and the canonical defer adapter instead. A shield that immediately propagates cancellation remains allowed because it does not retry.

### D5. Test externally visible ownership points

One regression cancels grouped terminal persistence while append and append-barrier work are independently blocked. It proves the append barrier completes, terminal delivery occurs, the delivery barrier completes, and then cancellation propagates. A second regression cancels session close while detached finalization waits for the registry lock and proves the exact session is removed before cancellation propagates.

## Risks / Trade-offs

- **Barrier callback failure can still fail cleanup**: Existing exception semantics are retained; this change addresses cancellation ownership, not callback error policy.
- **Cancellation is delayed through required cleanup**: This is intentional and bounded by the existing owned operations; no new retries or deadlines are introduced.
- **The static check is intentionally narrow**: It detects the known loop-plus-shield shape and does not attempt general control-flow proof for every cancellation pattern.
- **Main can advance again during review**: The PR remains small so a later rebase can reapply the two ownership deltas without restoring absorbed code.

## Migration Plan

1. Rebuild on current `main` and apply only residual ownership, tests, guard, and OpenSpec artifacts.
2. Run focused and repository validation, then force-with-lease update the explicitly rebase-requested fork branch.
3. Let hosted CI and current-head CodeRabbit review run before maintainer merge.
4. No data, settings, or deployment migration is required.
