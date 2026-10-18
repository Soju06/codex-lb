## Why

The HTTP bridge event flusher sleeps for another interval after each batch even when queued events remain. At the default batch size and interval, 320 already queued events take about 920 ms to persist with a synthetic 1 ms writer, including nine unnecessary 100 ms waits.

## What Changes

- Continue bounded background flush passes while eligible events remain queued.
- Preserve one batch per operation per pass, terminal ownership, failure handling and shutdown cancellation.
- Add public-interface regression coverage and reproducible before/after measurements.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Background transcript persistence does not wait for new events or another interval while eligible backlog remains.

## Impact

Only the Python HTTP bridge event batcher and its tests change. No dependency, setting, schema, frontend or transport change is required. Terminal timeout work in PR #1997 remains independently owned.
