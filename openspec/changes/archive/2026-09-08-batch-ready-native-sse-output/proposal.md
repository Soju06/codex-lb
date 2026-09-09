# Batch ready native SSE output

## Why

HTTP stream interpretation now lives in Rust, but every small SSE event still
locks and flushes Tokio stdout separately. The last synthetic measurement reduced
Python alias work without improving total elapsed time. Measure and reduce the
ready-event IPC write overhead while preserving stream behavior.

## What Changes

- Coalesce only already available SSE records into bounded output writes.
- Flush before another upstream body read and before terminal/error delivery;
  introduce no timer, delay, protocol grammar, capability or operator setting.
- Preserve a partially written output buffer across request cancellation so a
  subsequent writer completes whole JSON lines before writing its own records.
- Keep Python queue fairness, ordering, limits, cancellation and replay policy.

## Impact

Native egress output/SSE path, cancellation and direct/routed integration tests,
benchmark evidence, and outbound client ownership documentation. WebSocket event
interpretation remains the next domain migration slice after IPC optimization.
