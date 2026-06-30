## Why

Operators use `/internal/drain/status` to decide when a replica can be safely
removed. HTTP bridge sessions can have queued or in-flight work that is not
visible through the previous drain-status payload, and stale in-flight session
creation markers can make a draining replica look busy forever.

## What Changes

- Expose HTTP bridge activity counters in the internal drain-status payload.
- Clean stale or completed in-flight HTTP bridge session creation markers when
  building that activity snapshot.
- Keep the snapshot bounded and non-blocking so drain probes remain safe.

## Impact

- Drain automation can distinguish active bridge work from stale local markers.
- Operators get low-cardinality counts for pending bridge sessions and cleaned
  stale in-flight creates.
