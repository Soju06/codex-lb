# Change Proposal: Align the compact main-path budget with Responses streams

## Why

Compaction uses the same long-running upstream Responses protocol as ordinary
Responses streams, but its default 180-second request budget leaves only a
150-second upstream window after the settlement reserve. Slow valid compact
turns therefore fail locally before their upstream result arrives.

## What Changes

- align the default compact request budget with the bounded 7,200-second
  Responses stream budget;
- derive the upstream compact timeout from the existing
  `compact_request_budget_seconds` operator setting, without introducing a
  second timeout configuration.

## Impact

Valid long compactions retain the same bounded window as normal Responses
streams. Explicit smaller compact budget configuration and cancellation keep
their current behavior.
