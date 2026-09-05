# Round-19 performance and lifecycle evidence

## Revisions and method

- Pinned main: `5ad638b6a4c9c094bcc8866b1d7487173fe3b54e`.
- Delivered PR before this change: `4325978b598cfefe327e9620543f38a5a022a0ff`.
- Measured implementation after this change: `dfc7338ffea77784b654f69c4672f4139a7ba527`, clean tree `c8eb81d673d395a49c0c2291e12b1a047d4f4b47`.
- Benchmark script blob: `09b09a569a63a51d84152f6ba9573f9c676a47e7`.
- Same CPython 3.13.5 interpreter, macOS 27 arm64; 10,000 events per sample, five samples after a 1,000-event warmup.
- CPU uses process CPU time. Wall time includes event-loop scheduling. Task counts come from a separate loop task-factory pass, including tasks created through `ensure_future`.
- Raw samples: [main](benchmarks/main.json), [before](benchmarks/before.json), [after](benchmarks/after.json). The before record is dirty because the benchmark script was untracked; application source still matched the delivered head. The final after run is on the clean implementation commit.

Run the same committed script from the candidate environment with `--repo` pointing to each registered worktree:

```sh
uv run python scripts/benchmark_http_bridge_queue.py --repo .
uv run python scripts/benchmark_http_bridge_queue.py --repo /tmp/pr1903-perf-main-20260906
```

For the before control, use a separate worktree at `4325978b598cfefe327e9620543f38a5a022a0ff` and pass its path to the same script. Do not change the interpreter between runs.

The script imports the real bounded queue and reader on the PR. Main predates both helpers, so the baseline is its plain `asyncio.Queue` with `asyncio.wait_for(queue.get())`. This is a queue/reader microbenchmark, not HTTP throughput, native transport, or production fleet CPU.

## Results

Median microseconds per event. These are local measurements, not the maintainer's Python 3.14 measurements.

| Schedule | Main CPU | Before CPU | After CPU | Main wall | Before wall | After wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Producer-ahead | 1.2951 | 0.9196 | 0.8641 | 1.3030 | 0.9268 | 0.8737 |
| Interleaved | 8.4533 | 32.3554 | 11.5051 | 30.8565 | 100.6762 | 34.6645 |
| Burst | 1.3275 | 28.3829 | 18.9570 | 1.3332 | 85.1037 | 53.9898 |

| Schedule | Main tasks/event | Before tasks/event | After tasks/event |
| --- | ---: | ---: | ---: |
| Producer-ahead | 0 | 0 | 0 |
| Interleaved | 0.0001 | 3.0001 | 0.0001 |
| Burst | 0.0001 | 2.6668 | 1.0000 |

Producer-ahead publishes two events synchronously and consumes both. Interleaved uses producer demand/consumed signals and asserts the queue is empty when every wrapper read starts. Its one producer task contributes 0.0001 tasks/event. The after implementation creates no per-read child tasks.

The final interleaved run uses 64.4% less CPU than the delivered head, but remains 36.1% above main, an extra 3.05 microseconds/event. An earlier dirty run of the same implementation measured 10.7161 microseconds/event. The final immutable result above is the reported candidate, not the faster earlier sample.

Burst deliberately lets the producer run freely. Main retains all events in its unbounded queue without blocking; the candidate repeatedly blocks at two events. Their memory and scheduling behavior differ. The candidate's remaining task count comes from cancellation-safe blocked puts. The burst result does not demonstrate parity with main, and the cost remains real.

The process-wide byte-budget thread lock is retained. This experiment does not isolate its cost. No production 2-core or native slow-reader performance proof is claimed.

## Finding-by-finding disposition

1. Native slow-consumer abort: HTTP-bridge socket opens pass `use_native_egress=False` in direct, routed, and reconnect paths. Direct and Codex-routed client tests assert the bypass, while unrelated transports retain the native default. This is a compatibility fallback, not native per-stream flow control. The native 100-frame slow-reader reproduction has not been rerun against an implemented native fix because no native fix is present. Maintainer acceptance of the fallback remains required.
2. Read CPU fanout: buffered reads stay synchronous; empty reads await owned futures. Timeout cancellation stays in the reader task. Publication never consumes on the reader's behalf. The no-child-task test was red on the delivered implementation and is green now. The tables include both requested schedules and the remaining burst cost.
3. Delayed terminal delivery: the live generator's attaching state prevents finalization from discarding its revoked queue. The route regression pauses after submission, during cooldown, or during turn-state registration; each runs with both streaming and non-streaming responses. All six cases finish the failing sibling's finalization before releasing the first consumer, observe the actual liveness failure, and return byte credits to baseline.
4. Shared-reader deadlines: `test_paused_enqueue_deadline_allows_shared_reader_to_settle_expired_sibling` uses the real relay, real event dispatch, and real deadline/failure settlement. A full paused queue releases on its own deadline; the sibling receives `response.failed/request_timeout` plus EOS and pending state clears. Clearing the enqueue deadline in a red control made this test time out. This does not promise that a shorter sibling deadline settles before the paused owner's deadline.
5. Duplicate helpers: one terminal enqueue helper and one cancellation-deferring wrapper remain outside the batcher branch. The fallback uses the same revocation-aware delivery result.
6. Terminal contract: disposal requires explicit detachment or proven absence of a downstream owner. Delta/main specs and design preserve the actual terminal for a live delayed generator. The broad native-egress requirement now names the bridge exception.

## Verification and review boundary

Implementation `dfc7338ff` has the same application source tested before commit, apart from Ruff whitespace.

- Full `tests/unit`: 7,783 passed, 97 skipped. Skips include missing local Helm and three intentionally retired conflict tests.
- Full HTTP bridge and proxy WebSocket integration pair: 291 passed.
- Final wakeup, reader-deadline, and abort/EOS files: 15 passed. This includes two extra wakeup cases added after full-unit collection.
- `make lint`, including architecture/cancellation checks, and `uv run ty check`: passed.
- Strict OpenSpec 1.10.0 validation: targeted change and capability passed; all 58 main specs passed.
- `git diff --check`: passed.
- Full local `make ci` was not run because this task excludes its browser/container operations. Subset checks do not substitute for that repository workflow requirement. Exact-head hosted CI is the remaining full-gate evidence.

### Standards

No explicit candidate code/spec violations found for `4325978b..dfc7338ff`, tree `c8eb81d6`. Typed futures are removed or cancelled in `finally`; same-task consumption preserves cancellation ownership. Scope, OpenSpec separation, and native exception follow the bound repository rules.

The prescribed full local gate remains an explicit workflow exception under the task's execution constraints. Hosted verification is separate. Waking all readers is a cancellation-safety judgment, not a documented rule violation.

### Input

No actionable implementation blocker found in the reviewed changes. The read-side task-fanout cause is removed and tested. Native mitigation, residual CPU, and shorter sibling timing remain limited as described above. Maintainer acceptance cannot be inferred from local checks.

Review state is tied to the implementation commit; later evidence-only documentation does not change application source.

## Follow-up top-level P2 on delayed attachment

[CodeRabbit's follow-up](https://github.com/Soju06/codex-lb/pull/1903#issuecomment-5552300932)
states that the attaching flag starts only at attachment. Delivered
`4325978b` sets it at `streaming.py:4188`, the generator's first statement,
before submission, cooldown, and registration. Only explicit detachment or
successful attachment under the pending lock clears it. The current performance
implementation preserves this lifecycle.

The six-case route regression checks the exact named windows on both response
modes. It asserts attaching=true and started=false before each pause. Sibling
finalization completes before the consumer resumes. All six pass.

The red control clears attaching on the claimed requests immediately before
calling the real finalizer, recreating the unsafe disposal condition without
changing files. The streaming cooldown case fails on the exact route result:
`upstream_stream_truncated != upstream_websocket_liveness_timeout`.
The production code passes. No runtime change is justified by this finding;
the test expansion and explicit disposition address the proof gap.

The earlier full integration pair passed 291 tests. After this six-case
expansion, the full pair passed 296 tests in 184.15 seconds. Ruff, formatting,
type checking, and diff checks also passed. Runtime source is unchanged from
the measured implementation commit.
