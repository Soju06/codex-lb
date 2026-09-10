## Context

The WebSocket handler publishes one tracked `finalize_websocket_scope()` task
from its `finally` block. The task must preserve cancellation while completing
the terminal request cleanup sequence. During process drain,
`shutdown_state.remaining_drain_timeout_seconds()` provides the shared absolute
deadline. Outside drain it returns `None`, so the current code falls back to
the generic one-second `_TASK_CANCEL_TIMEOUT_SECONDS` value.

The generic timeout is used across HTTP bridge and proxy child-task cancellation
paths. Increasing it globally would slow unrelated cancellation and would
change the semantics of a helper that is intentionally a short cancellation
observation bound. The scope finalizer needs a different bounded allowance
because its work is a sequence of request-state and lease finalization steps.

## Goals / Non-Goals

**Goals:**

- Give normal direct WebSocket scope cleanup enough bounded time for the
  existing request finalization and lease-release sequence.
- Preserve the existing tracked-task ownership and cancellation behavior when
  the bound is reached.
- Keep shutdown cleanup governed by the one shared remaining drain deadline.
- Prove the behavior through the real WebSocket route finalizer.

**Non-Goals:**

- Changing the `response.created` watchdog or any upstream request budget.
- Retrying, replaying, or moving an interrupted request to another account.
- Increasing the generic `_TASK_CANCEL_TIMEOUT_SECONDS` value.
- Adding an operator setting, environment variable, database state, or a new
  background cleanup registry.

## Decisions

1. **Use one internal five-second scope budget.** The value is deliberately
   fixed and bounded because this is a lifecycle safety allowance, not an
   operator tuning surface. Five seconds is long enough to absorb ordinary
   persistence/lease scheduling variance while still returning promptly when
   teardown is stuck.

2. **Prefer the active drain deadline.** When shutdown drain is active, the
   finalizer continues to use the remaining shared deadline exactly as today.
   The normal-operation budget is only the fallback for the no-drain case and
   cannot extend process shutdown.

3. **Keep child cancellation semantics separate.** Individual task waits keep
   the one-second generic cancellation bound during normal operation. During
   drain they remain capped by the shared remaining deadline. Only the outer
   scope-finalization wait receives the five-second normal-operation allowance.

4. **Retain tracked cleanup after the bound.** `asyncio.wait()` continues to
   observe the finalizer without cancelling it at the scope budget. The
   existing `_background_cleanup_tasks` registry and persistence drain remain
   the owner of unfinished cleanup, so a timeout is honest and does not cause
   lease or request finalization to be abandoned.

## Verification Strategy

- Run the focused WebSocket terminal-cancellation tests, including a regression
  that lowers the generic task timeout and delays finalization beyond it while
  allowing completion within the separate scope budget.
- Run Ruff check/format on changed Python files, the proxy architecture check,
  and the applicable type/test targets.
- Validate the OpenSpec delta if the CLI is available; otherwise record the
  unavailable local CLI as a handoff limitation and keep the artifacts in the
  repository for CI validation.
