# Verification

## Completeness and correctness

- The Responses library owns compact SSE collection; egress composes it with
  framing, and Python retains public normalization/error translation.
- 20 shared collector fixtures agree with the original Python collector. The
  direct/routed public compact comparison runs every case through both native
  and missing-helper paths, including terminal errors and missing completion.
- A collected result exceeding 2 MiB arrives through more than 64 bounded
  fragments without Python event collection. Its unknown fields and 71-digit
  integer survive, and trailing oversized SSE does not invalidate completion.
- Existing compact tests prove cancellation before/after headers, timeout
  precedence, framing limits, resource ownership, and replay-safe endpoint use.
- Malformed, truncated, duplicate, oversized, or wrong-kind IPC results fail
  without replay. An installed helper lacking the new capability fails closed.
- Escaped surrogate keys remain opaque during assembly rather than panicking a
  worker task. Rust and direct/routed integration regressions verify preservation.


## Local validation

- Native adapter/client/packaging/shared fixtures and real-worker routed/SSE
  integration group: 249 passed; the invalid-option group also passed all six cases after adding
  the collection/content-type constraint.
- Existing compact client regressions: 101 passed.
- Rust workspace tests: 21 passed, including the 20-case collector fixture test.
- Locked release build, formatting, Clippy with denied warnings, cargo-deny,
  Ruff, changed-file ty, architecture/cancellation/timing checks passed.
- Strict OpenSpec delta validation passed before archival.

## Integration with current main

- Merged main through `9703ef9b1`, preserving the byte-bounded stream queue
  and the scheduling opportunity after every accepted native event.
- The large compact result regression fixes its queue capacity at 64 so it
  continues to exercise consumer progress after the default capacity increased.
- Buffered Responses SSE/JSON/error and adapter burst regressions also fix the
  queue capacity at 64; their consumers start reading immediately. This preserves
  over-capacity coverage for 256- and 2,000-event bursts after the main update.
- Combined adapter/client/shared fixture tests: 125 passed; compact route and
  packaging/framing fixture tests: 62 passed; real-worker integration: 119 passed.
- Release workspace tests: 21 passed. Release build, Ruff, changed-file typing,
  architecture/cancellation/timing checks, and all 58 strict specs passed.

## Performance scope

The reproducible benchmark is retained under
`~/benchmarks/codex-lb-compact/2026-09-08/compare_collectors.py`, with results
archived alongside migration evidence. It uses the same release helper and
loopback HTTP body for both native framing/Python collection and native
collection. 2,049 events, 270,260 upstream bytes, 30 measured sequential calls
per mode after warmup: median 431.6 ms versus 9.8 ms; p95 554.6 ms versus 13.9 ms.
Python process CPU averages 291.3 ms versus 4.8 ms per call; helper CPU is excluded.
This synthetic measurement is not a production latency or whole-service CPU claim.
At concurrency 8 (48 measured calls per mode), median latency was 3,441.1 ms
versus 40.5 ms, and p95 was 3,599.7 ms versus 49.2 ms.
RSS is recorded jointly for both modes and cannot establish a memory reduction.

## Coherence

The synchronous Responses crate has no networking or IPC dependencies. The
collector preserves opaque JSON and numeric index ordering. Existing wire
fields keep their meaning; the new optional behavior requires an additional
capability. No public configuration or deployment steps were introduced.
No outstanding implementation, scenario, or ownership gaps were found.
