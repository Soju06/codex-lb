# Change Proposal: Align the compact main-path budget with Responses streams

## Why

Compaction uses the same long-running upstream Responses protocol as ordinary
Responses streams, but its default 180-second request budget leaves only a
150-second upstream window after the settlement reserve. Slow valid compact
turns therefore fail locally before their upstream result arrives.

## What Changes

- align the default compact request budget with the bounded 7,200-second
  Responses stream budget;
- retain `upstream_compact_timeout_seconds` as an explicit operator cap that
  can shorten, but never extend, the per-request compact budget.

## Impact

Valid long compactions retain the same bounded window as normal Responses
streams. Explicit smaller compact timeout configuration and cancellation keep
their current behavior.
