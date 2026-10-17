## 1. Regression

- [x] 1.1 Add adapter tests for process reuse, concurrent request demultiplexing, per-request cancellation, helper death fan-out, next-request restart, and idempotent close.
- [x] 1.2 Add Rust protocol tests or deterministic integration coverage proving pooled client reuse and concurrent request/cancel commands.
- [x] 1.3 Add application shutdown coverage for persistent-helper cleanup.

## 2. Implementation

- [x] 2.1 Replace the one-shot Python adapter with one generation-aware multiplexed subprocess and per-request queues.
- [x] 2.2 Replace the one-shot Rust entry point with a long-lived command loop, shared reqwest client pool, concurrent request tasks, and targeted cancellation.
- [x] 2.3 Close the discovered helper during application shutdown without changing missing-helper startup behavior.
- [x] 2.4 Update operator documentation and protocol notes with the remaining per-worker and wire-level limits.

## 3. Verification

- [x] 3.1 Run focused and broad Python tests, formatting, lint, type checks, Rust checks/tests, Docker artifact build, and strict OpenSpec validation.
- [x] 3.2 Run a deterministic reuse probe showing multiple requests use one helper generation and one compatible reqwest client pool.
