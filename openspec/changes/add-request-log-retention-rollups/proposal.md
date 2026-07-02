# Add request-log retention rollups

## Summary

Add an operator-controlled request-log retention path that rolls old raw
`request_logs` rows into daily aggregate rows before any pruning. The default
runtime behavior remains unchanged until an operator runs the pruning command
in apply mode.

## Why

Long-lived hosted deployments can accumulate large SQLite databases from raw
request logs. Those raw rows are useful for recent debugging and continuity
lookups, but keeping them forever increases storage and can make dashboard
aggregation expensive. We need a safe way to shrink old detailed logs without
changing recent request continuity or losing historical usage totals.

## Scope

- Add a daily aggregate table for request-log usage totals.
- Add an operator-invoked retention/pruning service with dry-run and apply modes.
- Preserve raw request logs inside a minimum safety window.
- Keep retention dry-run by default.
- Add tests proving totals are rolled up before raw rows are deleted.

## Non-Goals

- Do not change upstream billing, account credit usage, or API-key admission
  accounting.
- Do not make dashboard endpoints read from aggregates in this change.
- Do not automatically compact SQLite files on every startup.
