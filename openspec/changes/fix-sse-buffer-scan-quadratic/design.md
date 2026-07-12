## Context

`_iter_sse_events` feeds every upstream SSE path (HTTP responses and bridge upstream reads). It buffered with `iter_chunked(1024)` and called `_pop_sse_event` → `_find_sse_separator(buffer)` after each read, which `bytes.find`s three separators from offset 0 over the whole accumulated buffer. While a large event accumulates, each 1 KiB read rescans everything already scanned — O(n²) with n up to `max_sse_event_bytes` (16 MiB default). The scanning is synchronous CPU on the event loop shared by all streams.

## Goals / Non-Goals

**Goals:** linear-time framing with identical semantics on all separator forms and boundary conditions.

**Non-Goals:** the parse-once SSE payload pipeline (separate, larger change); changing `max_sse_event_bytes`; zero-copy buffer management (the per-event `del buffer[:end]` memmove is unchanged and amortizes to one move per byte).

## Decisions

- **Cursor with 3-byte overlap** rather than restructuring into a ring buffer: `scanned` marks the prefix known separator-free; new scans start at `scanned − 3` to catch a `\r\n\r\n` straddling the boundary; the cursor resets to 0 after each popped event (the residual is freshly unscanned). Minimal diff, provably equivalent framing.
- **16 KiB reads**: balances latency (small deltas still flush immediately — aiohttp yields what's available, `iter_chunked` caps, not pads) against iteration overhead. Measured with the cursor: 8 MiB event = 0.18 s total.
- `_pop_sse_event` is kept for its unit tests; the hot loop inlines the cursor-aware scan.

## Risks / Trade-offs

- [Off-by-one in the overlap loses a straddled separator] → regression tests split each separator form across read boundaries and assert framing.

## Migration Plan

Code-only; rollback = revert.

## Open Questions

None.
