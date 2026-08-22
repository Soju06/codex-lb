## 1. Implementation

- [x] 1.1 Materialize ordered `response.output_item.done` items from the durable event spool.
- [x] 1.2 Persist the materialized output only after a terminal completion event is present.
- [x] 1.3 Capture output-item completions in the live bridge state before the
  terminal operation-state write, so the event batcher cannot persist an empty
  terminal array first.
- [x] 1.4 Persist a bounded self-contained replay-input snapshot for completed
  operations and add the schema migration.
- [x] 1.5 Prefer a retained replay snapshot when reconstructing a continuation
  whose upstream parent-response chain is unavailable.
- [x] 1.6 Persist the first/root Codex operation with a session-scoped
  fingerprint when complete-transcript recovery is enabled.

## 2. Verification

- [x] 2.1 Add focused unit coverage for empty terminal output and missing completion.
- [x] 2.2 Add coverage for snapshot bounds and recovery after parent-chain purge.
- [x] 2.3 Add coverage for root-operation registration and fingerprint scoping.
- [ ] 2.4 Run focused tests, lint, and strict OpenSpec validation.
