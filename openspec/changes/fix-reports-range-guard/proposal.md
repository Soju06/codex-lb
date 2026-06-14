## Why

`GET /api/reports` currently expands every requested calendar day into a bucket
list before querying daily aggregates. Because the endpoint accepts arbitrary
past dates, a caller can request an extremely large span and force excessive
memory use plus a large number of chunked UNION queries.

## What Changes

- Reject report requests whose inclusive date span is larger than the supported
  daily reporting window.
- Return a 400-class dashboard error before the backend expands per-day report
  buckets for oversized ranges.
- Add regression coverage for the oversized-range path.

## Impact

- Prevents resource exhaustion from pathologically large report requests.
- Keeps `/api/reports` behavior explicit instead of silently attempting to
  process unbounded daily windows.
- Adds an OpenSpec-backed contract for the supported report range.
