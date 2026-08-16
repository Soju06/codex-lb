# Tasks

## 1. Fork spillover

- [x] 1.1 Detect self-contained unanchored parallel forks rejected by a local account cap without crossing turn-state or forwarding ownership.
- [x] 1.2 Drop the preferred-account hint once and retry account selection.
- [x] 1.3 Emit a stable bridge event for the spill.

## 2. Tests

- [x] 2.1 Cover eligible and owner-bearing predicate cases.
- [x] 2.2 Exercise the HTTP bridge path through capped preferred-account selection, successful reselection, and turn-state-owned rejection.
