# query-caching Delta

## ADDED Requirements

### Requirement: API-key usage summaries combine a persistent rollup with a bounded live tail

API-key usage summaries MUST NOT aggregate the full `request_logs` history per read. The read MUST combine persisted per-key rollup sums (`api_key_usage_rollups`, folded by the same watermark and fold job as the account rollup) with a live aggregate constrained to rows newer than the watermark, preserving the API-key summary semantics on both portions: no duplicate collapsing, soft-deleted rows included, warmup kinds excluded, `cached ≤ input` clamp applied to merged totals.

#### Scenario: Folding does not change per-key totals

- **GIVEN** request-log rows attributed to an API key on both sides of the fold boundary
- **WHEN** a fold pass runs and per-key summaries are read afterwards
- **THEN** the totals MUST equal the pre-fold full-history aggregate

#### Scenario: Per-key totals survive request-log pruning

- **GIVEN** folded request-log rows attributed to an API key are deleted by retention
- **WHEN** per-key summaries are read afterwards
- **THEN** the totals MUST equal their pre-pruning values

#### Scenario: Sums and watermark are read in one snapshot

- **WHEN** per-key summaries are read while a fold slice may commit concurrently
- **THEN** rollup sums and the watermark MUST come from a single statement
- **AND** no qualifying row's contribution may be absent from both the rollup sums and the live tail of that read

### Requirement: API-key usage rollup rows follow the key lifecycle

Deleting an API key MUST delete its rollup row in the same transaction.

#### Scenario: Key deletion removes its rollup row

- **GIVEN** an API key with a rollup row
- **WHEN** the key is deleted
- **THEN** the rollup row MUST be deleted in the same transaction
