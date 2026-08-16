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
- [x] 1.18 Preserve acknowledged state after partial response output and disconnect.
- [x] 1.19 Stop indefinite recovery after a retry attempt emits downstream output.
- [x] 1.20 Include sequence position in event fingerprints so repeated SSE blocks survive replay.
- [x] 1.21 Close the event batcher flusher from the proxy shutdown path.
- [x] 1.22 Refuse cross-session handoff while the prior session lease is active.
- [x] 1.23 Keep eventless local transport failures retryable in indefinite recovery.
- [x] 1.24 Terminalize and persist `response.incomplete` operation outcomes.
- [x] 1.25 Place all response-compatibility requirements in the capability delta path.
- [x] 1.26 Record timeout health only after pending reservation settlement.
- [x] 1.27 Replay finalized incomplete operations without resetting their terminal spool.
- [x] 1.28 Return reservation settlement status to timeout health handling.
- [x] 1.29 Revalidate the final response.create frame after operation metadata injection.
- [x] 1.30 Require an inactive unknown operation before same-session recovery reset.
- [x] 1.31 Keep operation transcript retention active when sticky mapping cleanup is disabled.
- [x] 1.32 Persist and fence the one-shot recovery dispatch budget through
  replacement-session handoff, rollback, and terminal settlement.
- [x] 1.33 Restore claimed recovery operations on every pre-admission exit and
  atomically spool deterministic terminal failures before exposing `failed`.

## 2. Validation

- [x] 2.1 Add or update focused repository and request-submit regressions.
- [x] 2.2 Run focused HTTP bridge tests, Ruff, Ty, diff checks, and strict
  OpenSpec validation.
  - Evidence: focused HTTP bridge/API tests, Ruff, Ty, migration checks, and
    strict OpenSpec validation passed after the recovery-budget handoff fix.
- [x] 2.3 Verify disabled sticky cleanup still runs durable transcript retention.
- [x] 2.4 Add regressions for pre-admission claim restoration and terminal
  failure spool/state ordering.
