## Context

See `proposal.md` for motivation and `context.md` for the observed SQLAlchemy lifecycle. Candidate selection and per-account refresh deliberately use separate repository sessions, but the current handoff carries ORM instances rather than the scalar identity each worker needs.

## Goals / Non-Goals

**Goals:**

- Preserve the existing short candidate-query transaction and separately owned refresh sessions.
- Make the regression executable against a real async SQLAlchemy session lifecycle.

**Non-Goals:**

- Change guardian eligibility, scheduling, leader election, batching, concurrency, backoff, or refresh semantics.
- Change shared session cleanup behavior for unrelated background tasks.

## Decisions

### Snapshot scalar IDs inside the candidate-query session

Selection, backoff filtering, and batch truncation will produce a list of account ID strings before the repository context exits. Workers will receive those strings and keep the existing fresh `get_by_id` step.

Keeping the selection session open through `asyncio.gather` was rejected because it lengthens the transaction and would tempt concurrent tasks to share one `AsyncSession`. Changing global rollback/expiration behavior was rejected because the guardian does not need detached ORM objects and shared cleanup semantics have broader risk.

### Reproduce the real lifecycle at the scheduler boundary

The regression test will construct a real async SQLAlchemy repository session whose context cleanup rolls back and closes it, then invoke one guardian pass through the scheduler interface. Existing fakes remain useful for scheduling cases but cannot model ORM expiration and detachment.

## Risks / Trade-offs

- [A selected account changes before its worker starts] → Keep the existing per-worker re-read and eligibility check.
- [A test-only session lifecycle diverges from production cleanup] → Use the same rollback-then-close behavior that production `close_session` applies rather than manually expiring one attribute.
- [The fix accidentally changes batch ordering or limits] → Derive IDs only after the existing ordering, backoff filtering, and truncation steps.
