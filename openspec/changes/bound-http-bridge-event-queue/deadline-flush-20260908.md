# Terminal flush deadline correction

## Candidate and scope

Originating finding: [discussion 3952711236](https://github.com/Soju06/codex-lb/pull/1903#discussion_r3952711236),
reviewing `1365234c7ea0ba7a561fac1aecf55e5312f2791e`.
Independent reviews below cover tree
`54d96969515e6e1c7c98a9d58067721505080a08` against that head.
The subsequent tree `eb69a36a9cf5b750dcdf941e36f169c9eda6efa6` only
moves the new scenarios after normative paragraphs in both specs.
Application and test content is identical. Final evidence and checklist
updates do not change the reviewed implementation.

The checked upstream target is `15ccd901bf013daa11c67934cc019a9b33f2f72b`.
The candidate's merge base remains `35ccf8e9369b10e5819229ac674a3e1c5f6569ae`.
No rebase was performed just because upstream advanced.

## Reproduction and cause

The real bridge stream consumed `response.created` and paused. Three
reasoning events were deferred through the real upstream dispatcher. The
terminal frame flushed two events into the finite queue, then its third
payload expired at the enqueue deadline. The old code revoked producers but
forgot why. It subsequently published `response.completed` out of band.
The consumer received success after losing the third event.

The initial regression failed its no-success assertion on the original
implementation in 0.68 seconds. It used the dedicated database launcher and
the integration test selector `terminal_flush_deadline`. The expanded
regression covers real and virtual clocks and both HTTP-error propagation
modes. It observes the actual stream generator's retained prefix followed by
`response.failed/request_timeout`, then EOS, with no missing delta disguised
as success.

The queue now remembers deadline-related payload loss and publishes an
ordered timeout failure without waiting for a consumer slot. That cause
survives consumption of the failure, blocking later terminal replacement.
An expiry involving only EOS preserves an already accepted terminal payload.
The existing byte-budget failure remains the fallback if retaining the timeout
payload also exceeds the byte budget.

Producer revocation alone still does not prove abandonment. The prior
failed-sender versus delayed-sibling distinction is unchanged. Upstream
terminal persistence and settlement still receive the original upstream
result; the timeout describes this live downstream delivery, not a rewritten
upstream execution result. No retry, account-selection, sibling-deadline, or
native-flow-control policy changed.

## Verification

- The affected queue, terminal, cancellation, reader-deadline, and simulation
  files passed all 66 tests.
- The broad bridge unit file and both HTTP/WebSocket integration files passed
  1,348 tests. One old `SimpleNamespace` fixture failed because it lacked the
  response identity needed to construct the new truthful failure. It now uses
  the actual request-state type and checks the timeout plus EOS. Its separate
  final targeted rerun passed in 1.56 seconds. Thus every affected case passed
  across the broad run and the corrected-fixture rerun. This is not described
  as a single all-green suite run. No application change followed the broad run.
- Four product-path cases verify real/virtual deadline expiry and both
  response modes. Virtual cases establish no early revocation, producer
  completion without another read, and task/timer cleanup. Request-log tasks
  are awaited separately because their database I/O uses the real event loop.
- Unit controls cover already-expired full and empty queues, refusal of late
  completion after failure consumption, EOS-only expiry, and exhausted
  failure-payload budget. A terminal persistence control proves the original
  upstream completion is appended and settled despite rejected live delivery.
- Ruff, formatting, type checking, architecture, cancellation safety, timing
  seams, diff checks, and strict change/affected-spec validation passed.

Both database URLs were set to the same dedicated absolute temporary SQLite
path before Python imports. Foreground, background, and conftest engine paths
were verified there before reset-capable tests. No host/live database,
incident snapshot, volume, browser, or container was accessed. The two pytest
warnings concern plugins preimported by this safety check.

No benchmark was rerun. Normal reads, puts, byte accounting, and scheduler
behavior are unchanged. The new helper only executes after an enqueue
deadline expires; terminal publication adds one failure-latch guard. Existing
benchmark samples remain tied to their original implementation and do not
establish CPU parity for this head.

## Standards

No explicit Standards violation found in the reviewed tree.

- OpenSpec-first rules require code/spec synchronization. Both specs define
  timeout failure after rejected payload delivery, retained-prefix ordering,
  budget fallback, upstream settlement, and the EOS-only exception.
- `AGENTS.md:109` requires cleanup and settlement after partial failure. The
  change preserves revocation and adds a persistent delivery-failure latch.
  The persistence regression checks upstream completion append and settlement
  while downstream receives the timeout failure.
- `AGENTS.md:123` requires regression coverage at the failing product path.
  The integration regression drives preparation, terminal dispatch, and the
  bridge stream generator with real/virtual timing and both error modes.
- Project conventions require reuse. Failure handling reuses terminal
  publication, byte accounting, and revocation.
- PRINCIPLES P1-P5 adds no gate here. No setting, setup requirement, README,
  or dashboard change is introduced.

Judgment only: the expiry helper uses `Any` for the request-state shape,
following surrounding helper conventions. Explicit typing would improve it
but is not a demonstrated hard-rule violation.

Verification was unfinished during review. Focused/static checks passed;
the fixture rerun, broader suites, and exact-head hosted gates were pending.
The reviewer ran no tests or application imports.

## Input

No actionable finding in the reviewed tree.

The change addresses the reported failure at the actual SSE boundary. The
integration regression drives real reasoning deferral, pauses the attached
generator, expires the blocked terminal flush, and verifies the retained
prefix followed by timeout failure and EOS. Both real/virtual timing and
HTTP-error propagation modes are covered.

The expiry helper distinguishes payload loss from EOS-only expiry. The queue
latch survives failure consumption, preventing later completion from reopening
delivery. Existing benign revocation and preconsumer handling are unchanged.

The persistence control verifies rejected downstream completion still appends
and settles the original upstream completion. Unit controls cover expired
queues, consumed failure, accepted-terminal/EOS expiry, and budget fallback.
The contract separates downstream delivery failure from the upstream result.
No new sibling-deadline, native-flow-control, or performance policy appears.

Evidence limits: submission is mocked in the real stream/dispatcher test.
Persistence and settlement collaborators are mocked in the separate control.
The broader suite and exact-head delivery gates were pending during review.
The reviewer ran no tests, imports, or writes.

Standards: 0 explicit violations. Input: 0 actionable findings.

Exact-head hosted CI, review responses, thread dispositions, and mergeability
are recorded in the PR delivery reply. Maintainer acceptance and approval
remain separate. No merge or deployment is included.
