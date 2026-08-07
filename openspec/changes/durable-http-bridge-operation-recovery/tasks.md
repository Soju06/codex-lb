## 1. Implementation

- [x] 1.1 Scope operation fingerprints and lookups by API-key namespace.
- [x] 1.2 Preserve recoverable operation sessions during startup takeover.
- [x] 1.3 Reset failed-operation event spools atomically.
- [x] 1.4 Gate sibling continuation anchoring on matching fingerprints.
- [x] 1.5 Merge the operation-ledger migration lineage with latest main.
- [x] 1.6 Keep SQLite event-spool defaults conservative and explicit.
- [x] 1.7 Retain completed transcripts through startup takeover and drain
  periodic retention batches.
- [x] 1.8 Reset partial spools before indefinite recovery retries.
- [x] 1.9 Persist deferred reasoning events in downstream order.
- [x] 1.10 Classify shared-websocket disconnects per operation event count.
- [x] 1.11 Expire stale submitted and acknowledged operation rows.
- [x] 1.12 Preserve acknowledged state after alias persistence failure.
- [x] 1.13 Rebind nonterminal cross-session operations before recovery reset.
- [x] 1.14 Protect actively leased operations during retention cleanup.
- [x] 1.15 Keep event-spool settings compatible with legacy test doubles.
- [x] 1.16 Gate indefinite recovery to eventless anchored operations.
- [x] 1.17 Convert recovery reservation failures into terminal SSE events.

## 2. Validation

- [ ] 2.1 Add or update focused repository and request-submit regressions.
- [ ] 2.2 Run focused HTTP bridge tests, Ruff, Ty, diff checks, and strict
  OpenSpec validation.
