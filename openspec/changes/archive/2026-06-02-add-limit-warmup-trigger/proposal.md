# Proposal: add-limit-warmup-trigger

## Why
Operators who rotate several Codex accounts can lose usable time when a refreshed upstream limit window does not effectively start until the next real request. codex-lb already tracks reset timing from usage refreshes, but it does not offer a safe way to trigger a newly available window without waiting for user traffic.

## What Changes
- Add an opt-in, reset-confirmed limit warm-up mechanism for primary (5h) and secondary (weekly) windows.
- Persist per-account opt-in, global warm-up settings, and one warm-up attempt record per account/window/reset.
- Send one minimal upstream Responses request only after usage refresh has observed a new available window that follows an exhausted sample.
- Surface account warm-up status in account summaries and dashboard account cards.

## Impact
- `usage-refresh-policy`: defines reset-confirmed warm-up behavior and safety constraints.
- `frontend-architecture`: adds dashboard controls and status display.
- `database-migrations`: adds warm-up persistence columns/table.
