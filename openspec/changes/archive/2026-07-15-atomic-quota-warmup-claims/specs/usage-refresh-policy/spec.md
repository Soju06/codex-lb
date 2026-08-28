# Delta Specification: usage-refresh-policy

## ADDED Requirements

### Requirement: Warm-up attempt dedup is atomic across processes

Limit warm-up attempt deduplication SHALL be enforced by the database as a
single atomic statement so that it holds across replicas and across processes
sharing one SQLite file: the tolerance-window duplicate check and the attempt
insert MUST NOT be separable into a check-then-act sequence observable by
concurrent writers, and on PostgreSQL concurrent attempt inserts for the same
account and window MUST be serialized. Two warm-up attempts whose `reset_at`
values differ by no more than the configured tolerance MUST NOT both persist
for the same account and window. Per-process locks MAY additionally throttle
local writes but MUST NOT be the mechanism that guarantees dedup.

#### Scenario: Two processes observe near-duplicate reset candidates

- **GIVEN** two refresh workers in separate processes share one database
- **AND** both observe the same account and window with `reset_at` values that
  differ by less than the configured tolerance
- **WHEN** both record a warm-up attempt concurrently
- **THEN** at most one attempt row persists for that account/window tolerance
  window
- **AND** the losing worker receives no attempt and sends no warm-up probe

#### Scenario: Exact-tuple duplicates remain constrained

- **GIVEN** a warm-up attempt already persists for an account, window, and
  `reset_at` tuple
- **WHEN** another worker inserts the identical tuple despite the atomic guard
- **THEN** the unique constraint rejects the duplicate
- **AND** the worker treats the rejection as a dedup skip rather than an error
