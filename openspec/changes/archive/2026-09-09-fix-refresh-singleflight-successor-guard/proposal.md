# Fix refresh singleflight successor settlement

The refresh singleflight negative cache must not publish a failed attempt's
error after a successor refresh has replaced that attempt for the same key.
This keeps callers arriving during the successor refresh joined to the live
operation instead of serving stale failure state.

## Scope

- Guard negative-cache writes and clears with the same current-task check that
  guards inflight removal.
- Add the successor-race regression coverage already exercised by the F8
  bughunt probe.
- Do not change downstream account-status failure handling.
