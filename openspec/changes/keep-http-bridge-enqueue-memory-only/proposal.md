## Why

Ordinary transcript enqueue currently shares the lock held by durable append,
so database latency can stall live streaming even when ownership is unchanged.

## What Changes

- Allow ordinary same-owner, same-generation events to enter the bounded memory
  queue while an earlier append is in flight.
- Keep ownership changes, recovery fences, and terminal drains serialized.
- Add blocked-append regression coverage without weakening rebind safety tests.

## Impact

- Affected spec: responses-api-compat.
- Affected code: HTTP bridge event batcher and its tests.
- No settings, schema, or deployment changes.
