# Ready SSE output batching

## Design and limits

The helper previously flushed Tokio stdout once per SSE record. Ready events now
share an output write, capped at 32 records and 64 KiB of encoded JSON lines.
Each existing 16 KiB upstream read slice flushes before processing another slice
or awaiting upstream input. A single existing record can exceed 64 KiB after
JSON escaping; it is emitted alone after the preceding batch. No timer or new
operator setting is involved. Compact, raw HTTP and WebSocket records retain
their immediate emission paths.

The shared writer owns the accepted bytes and write offset across producer
cancellation. A subsequent writer first completes that buffer, including the
underlying buffered flush. This prevents a cancelled large write from leaving a
truncated JSON line in front of a sibling event or cancellation acknowledgement.
Output failures still propagate through the existing error path. Unaccepted
request-local records may be discarded on cancellation.

For example, 257 ready deltas followed by an upstream pause arrive in order
before that upstream is allowed to finish. A valid event followed by an oversized
event in the same read is flushed before the typed size error. The IPC grammar,
capabilities, Python queue budgets/fairness, retry policy and event text bounds
remain unchanged.

## Verification (2026-09-08)

- `make rust-check rust-audit`: formatting, warning-free workspace Clippy,
  24 Rust tests, release worker build, and dependency audit passed.
- 528 related Python tests passed across native HTTP/SSE/routed transport,
  SSE/compact/stream fixtures, WebSocket client and streaming timeout behavior.
  The final expanded quiet-upstream test passed all four direct/routed cases
  (one event and an ordered 257-event burst).
- Shared-writer tests force cancellation after a partial write using an 8-byte
  duplex pipe, both while writing directly and while flushing a larger buffer.
  The complete accepted record always precedes the sibling record.
- Existing public oversized-event and cancellation/sibling tests passed.
- Ruff, `ty check` with the project Python environment, architecture,
  cancellation-safety, timing-seam and simplicity-budget checks passed.
- Strict change validation passed before archiving; strict main-spec validation
  is also required after syncing the delta.

## Synthetic performance evidence

Release helpers were built with Rust 1.96.0 from baseline `e987c56c8` and this
change, using identical Python code and uvloop. A loopback chunked HTTP server
sends 2,048 UTF-8 text deltas per request. Each shape uses four warmups and 20
measured samples with alternating baseline/candidate order. The consumer checks
event count and normalizes/classifies every event. Times below are medians in ms.
Parent CPU includes the loopback server; helper CPU uses `/proc` clock ticks.

| Workload | Elapsed before/after | Parent CPU before/after | Helper CPU before/after | First event before/after |
|---|---:|---:|---:|---:|
| Canonical, one request | 433.03 / 31.33 | 139.44 / 30.43 | 490 / 20 | 1.46 / 1.49 |
| Alias, one request | 452.09 / 31.86 | 136.06 / 30.83 | 495 / 20 | 1.39 / 1.60 |
| Canonical, four concurrent | 1656.28 / 127.13 | 519.61 / 126.61 | 1875 / 80 | 2.94 / 3.97 |

Elapsed medians improve 92.3–93.0% in these ready-event bursts. First-event
latency increases by about 1 ms at concurrency four; batching does not promise
identical scheduler latency. The separate quiet-upstream regression verifies
that output never depends on future input. These are shared-host measurements
without model generation delay, not production latency predictions.

A separate one-request `strace` run counted **2,051 → 74 stdout write calls**,
including handshake/headers/end records. Traced timings are excluded above.
Reproduction harness, raw measurements, traces and source/check manifests are
preserved under the host's curated project storage at
`projects/codex-lb/rust-migration/2026-09-08-native-sse-ipc/`.

Binary SHA-256 values:

- Baseline: `868a3db6dca23c694c12f4516919bd74a080a2e5a4dd690b2789a39f2052f669`
- Candidate: `7c612377a1f56e716531b764c76343ad8e85a0f924889622162c4ca1c4230fa1`

WebSocket event interpretation remains the next domain migration slice.
