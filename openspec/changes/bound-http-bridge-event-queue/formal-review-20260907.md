# September 7 formal review follow-up

## Bound candidate

This review covers tree `14363ce86086297630242e2c83ceaac7cac433d3`
against delivered head `a53b6fe59ca218baceb44db722e1b49e3ea84716`.
The upstream base remains `35ccf8e9369b10e5819229ac674a3e1c5f6569ae`.
The final commit adds this evidence and completes local checklist items.
The reviewed application and test content is unchanged.

CodeRabbit's formal review `5129927835` arrived after its clean top-level
reply. The formal review raised two findings, so the earlier reply did not
clear delivery. Both conditions also existed at the prior head `3bfe0f8d`.

## Failed sender disposition

The condition at `request_submit.py:880` applies only to the failed sender.
Its only production caller awaits liveness settlement and then raises the
selected `ProxyResponseError`. That sender cannot resume queue consumption.
The separate terminal finalizer preserves delayed sibling queues while their
attaching marker is set. The six delayed-sibling route regressions cover that
distinction and the sender's direct 502.

The condition remains unchanged. CodeRabbit verified the call path and
[withdrew the finding in-thread](https://github.com/Soju06/codex-lb/pull/1903#discussion_r3948141054).

## Confirmed cooldown cleanup gap

Post-submit startup cooldown returned before the consumer's cleanup
`try/finally`. Its direct lower-level detach left the attaching marker set
and bypassed the established shield, activity update, and idle-lease check.
It now calls `detach_downstream_request()`.

Four bridge-generator regressions cover SSE and propagated HTTP errors, each
with and without cancellation during detach. All four failed on the old
implementation and passed after the helper substitution. The final test uses
`anyio.sleep(0)` as its cancellation checkpoint because the type checker does
not resolve `anyio.lowlevel.checkpoint` correctly in this environment.

The test exercises real queue detachment and observes completed cleanup,
cleared ownership, updated activity, and idle-lease reconciliation. Submission
and the lease check are mocked, so it does not independently prove actual
lease release or byte-budget reclamation. Successful old lower-level cleanup
already discarded the queue; no unconditional byte or lease leak is claimed.

## Standards

No explicit Standards violation found in this follow-up.

Reviewed staged tree `14363ce86086297630242e2c83ceaac7cac433d3` against
`a53b6fe59ca218baceb44db722e1b49e3ea84716`; verified the index matches that
tree. Earlier unaffected review remains applicable.

- `AGENTS.md:109` requires async lifecycle changes to preserve cleanup after
  partial failure. The substitution uses the existing shielded cleanup helper,
  clearing attachment ownership, revoking delivery, detaching, updating
  activity, and checking idle-lease release.
- `.agents/skills/project-conventions/conventions.md:30` requires reuse rather
  than duplicate logic. Reusing `detach_downstream_request()` satisfies it.
- `AGENTS.md:123` requires regression coverage at the failing product path.
  The new test drives the bridge stream generator, exercises the real detach
  implementation, and checks SSE/HTTP-error outcomes with and without
  cancellation.
- OpenSpec follow-up tasks remain unchecked at the review boundary, so that
  candidate does not claim completed verification or hosted delivery.

Judgment only: the regression also inspects private ownership state and mock
call counts despite the public-behavior preference in `conventions.md:70`.
Those assertions target resource cleanup directly and accompany observable
stream/error assertions, so they are justified here.

This is a static review, not independent execution proof. Supplied checks
pass; affected suites and exact-head hosted gates remain pending at review.

## Input

No actionable finding in the bound tree against the delivered head.

The change at `streaming.py:4740` fixes the reported bypass by calling the
existing cleanup helper. That helper clears attaching ownership, revokes
delivery before awaiting detach, shields cleanup from surrounding AnyIO
cancellation, updates `last_used_at`, and reconciles idle lease eligibility.
It runs only after the branch has selected its terminal cooldown outcome,
preserving the distinction between delayed consumers and consumers closing.

The four regression cases exercise both HTTP-error propagation modes, with
and without cancellation during detach. They call the real detach
implementation and assert completed cleanup, cleared queue ownership,
updated activity time, and idle-lease reconciliation.

Evidence limits: submission and idle-lease reconciliation are mocked, and the
test uses an empty plain queue. It proves this branch invokes and completes
the established cleanup sequence; it does not independently prove actual
lease release or byte-budget reclamation. No broader claim is needed for
this one-line fix.

The supplied red/green result precedes the checkpoint substitution.
Current-candidate test execution remained pending during this static review.

Standards: 0 explicit violations. Input: 0 actionable findings.

## Verification after review

The full bridge unit file plus HTTP bridge and proxy WebSocket integration
files passed: 1,345 tests in 221.93 seconds, including the four final regression
cases. The only warnings concerned the two pytest plugins preimported by the
database-safety launcher. Both database URLs and all three engine targets were
verified inside the dedicated temporary directory before execution.

Ruff, formatting, type checks, proxy architecture, cancellation safety, timing
seams, diff checks, and strict OpenSpec change validation passed. No host/live
database, incident snapshot, volume, browser, or container was accessed.

The earlier full unit/simulation proof and serial benchmark samples remain
revision-bound in `integration-20260907.md`. This follow-up changes only the
selected cooldown exit, not the queue hot paths exercised by the benchmark.
It therefore does not justify rerunning or relabeling those measurements.
New-head hosted CI and review are required and recorded in the PR delivery
reply. No merge or deployment is included.
