## Why

The upstream SSE reader accumulates bytes in 1 KiB reads and, after every read, rescans the **entire** buffer from offset 0 for event separators (`app/core/clients/proxy.py`). A single multi-megabyte event (image output, large reasoning delta — the configured cap is 16 MiB) therefore costs O(n²) byte scanning on the event loop: measured 70.5 s of blocking scan for one 8 MiB event, freezing every in-flight stream on the instance for that long.

## What Changes

- Track a scan cursor: each read scans only the new bytes plus a 3-byte overlap (the longest separator is 4 bytes, so a separator can straddle a read boundary by at most 3). Measured: the same 8 MiB event drops from 70.5 s to 0.18 s (~400×), linear in event size.
- Raise the SSE read chunk size from 1 KiB to 16 KiB, cutting per-event iteration counts ~16×.
- No behavior change: identical event framing (all three separator forms, straddled separators included), size-limit enforcement, and idle-timeout handling.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `outbound-http-clients`: upstream SSE stream reads MUST scan each received byte a bounded number of times (no full-buffer rescans per read), so one large event cannot stall the shared event loop.

## Impact

- **Code**: `app/core/clients/proxy.py` (`_iter_sse_events`, `_find_sse_separator`, read chunk size). No API/schema change.
