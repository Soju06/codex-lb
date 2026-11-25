# September 7 upstream integration

## Candidate and scope

- Base: `35ccf8e9369b10e5819229ac674a3e1c5f6569ae`.
- Prior delivered head: `3bfe0f8d02be6876269b5878d1b82ea5564c80c3`.
- Tested, measured, and independently reviewed implementation:
  `103f624c5b511f0732fc0122cde44be3cb86ff53`, tree
  `301e91a2a349a98e3c53c30111a42274064dd14c`.

The prior branch remains intact locally. Its net queue change was replayed as
one focused commit onto current main. Conflicts in the bridge mixin, request
submission, streaming, and unit tests preserve both the existing queue contract
and upstream's clock/scheduler injection from #2103 and #2104. The capacity
terminal and coalesced usage-refresh changes from #2124 and #2125 remain intact.

Queue producer/revocation/cleanup tasks use the injected scheduler. Enqueue
deadlines use the injected clock and scheduler, including deferred, advisory,
grouped, and terminal publication. A narrow `Scheduler.timeout` context keeps
read consumption in the caller even at nonpositive timeouts. Production uses
the original `asyncio.timeout` function by identity; simulation reuses its
existing virtual timeout implementation. Required-keyword checks cover the four
new queue helpers without increasing any raw-timing allowance.

Native bypass remains limited to HTTP-bridge direct, account-routed, and
reconnect sockets. Attaching ownership still starts before submission,
cooldown, or registration can suspend the generator. The paused request's own
deadline bounds enqueue; this does not promise earlier settlement of a shorter
sibling deadline while the shared reader remains blocked.

## Local verification

All Python processes started with both database environment variables set to
the same absolute, dedicated temporary SQLite URL. The launcher verified the
foreground engine, background engine, and imported test fixture engine targets
before allowing any reset-capable test. No host history database, incident
snapshot, live database, volume, or container was accessed.

- Full unit and simulation suites: 8,084 passed, 98 skipped, 3 expected failures
  in 177.44 seconds. Skips cover unavailable local Helm, retired conflict cases,
  and a Python-3.14-only shield-callback canary. Expected failures cover two
  known AnyIO 4.13 lock cases and redundant reservation-release calls already
  documented by upstream's simulation suite.
- Full HTTP bridge and proxy WebSocket integration pair: 297 passed in 174.73
  seconds. This includes all six delayed-consumer windows and response modes.
  One test emitted an aiosqlite worker/closed-loop teardown warning; assertions
  passed. The two other warnings concern preimported pytest plugins used by the
  safety launcher.
- Real/virtual shared-reader deadline proof verifies no early revocation,
  sibling `response.failed/request_timeout` plus EOS, ordered retained events,
  empty pending ownership, and settled virtual tasks/timers.
- New real/virtual queue tests prove zero read child tasks for negative, zero,
  and positive timeouts; late payload retention; external cancellation;
  selected terminal plus EOS after revocation; and timer cleanup.
- Ruff lint/format, `ty`, proxy architecture, cancellation safety, timing seams,
  and `git diff --check`: passed.
- OpenSpec 1.11.0 strict change validation and strict validation of both affected
  capabilities: passed. The CI command, `validate --specs`, passes all 58 specs.
  Full strict validation reports 22 unchanged placeholder-purpose warnings on
  both the candidate and a clean worktree at the pinned base. Those unrelated
  specs were not edited to suppress warnings.

The first broad unit/HTTP-bridge run exposed a missing prewarm scheduler lookup
and an upstream test double with a terminal queue different from its request's
queue. Both were corrected before the complete passing runs above. A file-owner
test that failed during that first run passed in isolation and in the complete
unit suite; no routing policy was changed.

Full local `make ci` remains excluded because it includes browser/container
operations. Local subset checks do not replace the repository's full gate.
Exact-head hosted CI and CodeRabbit review are checked at delivery.

## Serial performance comparison

The committed benchmark script is unchanged, blob
`09b09a569a63a51d84152f6ba9573f9c676a47e7`. All revisions used CPython 3.13.5,
macOS arm64, 10,000 events, and five samples per schedule after 1,000 warmup
events. Task counts use a separate loop task-factory pass. Each revision was
measured in a clean registered worktree, with the same isolated import checks.
No local test suite ran during the measurements.

The first serial comparison ran main, prior, then rebased. Its interleaved
difference was large enough to warrant a run-order check. The second comparison
ran rebased, prior, then main. Both are retained. An exploratory overlapping
process run was excluded from these comparisons.

Median process CPU microseconds per event:

| Schedule | Main, first | Prior, first | Rebased, first | Main, reversed | Prior, reversed | Rebased, reversed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Producer-ahead | 1.2491 | 0.8348 | 0.8603 | 1.2190 | 0.8008 | 0.8141 |
| Interleaved | 7.5259 | 9.9764 | 11.9624 | 7.9786 | 9.7636 | 9.7924 |
| Burst | 1.2546 | 18.9531 | 19.9239 | 1.3767 | 14.9036 | 14.8034 |

| Schedule | Main tasks/event | Prior tasks/event | Rebased tasks/event |
| --- | ---: | ---: | ---: |
| Producer-ahead | 0 | 0 | 0 |
| Interleaved | 0.0001 | 0.0001 | 0.0001 |
| Burst | 0.0001 | 1.0000 | 1.0000 |

The interleaved producer accounts for the single task per 10,000 events.
Read-side task fanout remains zero. The first serial run measures 19.9% more
interleaved CPU than the prior head; the reversed run measures 0.3% more. These
samples do not establish CPU parity or a stable causal cost for the timing
integration. The rebased queue still costs more than unbounded main in that
schedule, 22.7% to 58.9% across these two comparisons.

Burst is not memory-equivalent. Main retains the whole burst in an unbounded
queue while the candidate repeatedly blocks at two live events. Remaining
candidate task creation comes from cancellation-safe blocked puts. The shared
byte-budget thread lock remains. No native slow-reader, production throughput,
fleet CPU, or 2-core host performance claim is made.

Raw samples, including wall times:

- First serial order: [main](benchmarks/20260907-main.json),
  [prior](benchmarks/20260907-before.json), [rebased](benchmarks/20260907-after.json).
- Reversed serial order: [main](benchmarks/20260907-reverse-main.json),
  [prior](benchmarks/20260907-reverse-before.json),
  [rebased](benchmarks/20260907-reverse-after.json).

## Review and acceptance

[Independent standards and input reviews](review-20260907.md) found no explicit
standards violation or actionable implementation finding at `103f624c5`.
Subsequent evidence-only documentation does not change the reviewed application
source. Hosted results belong to the final delivered head and are recorded in
the PR delivery reply.

Maintainer acceptance of the native compatibility fallback and remaining
performance cost is still required. Nothing here merges, deploys, changes
production policy, or treats local proof as that acceptance.
