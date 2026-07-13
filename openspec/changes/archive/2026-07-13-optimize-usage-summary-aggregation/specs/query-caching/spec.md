# query-caching Delta

## ADDED Requirements

### Requirement: Usage-summary window metrics aggregate in SQL

The usage-summary endpoint MUST NOT hydrate the secondary-window request-log rows into ORM objects for Python-side summation; window metrics and cost MUST come from SQL aggregates that reproduce the log-helper semantics exactly (output-token reasoning fallback, per-row cached<=input clamp, exclusion of NULL-cost rows from per-model cost, warmup exclusion).

#### Scenario: SQL aggregate equals the legacy summation

- **GIVEN** window logs covering reasoning-token fallback, cached tokens exceeding input, negative cached tokens, NULL inputs, NULL costs, and warmup rows
- **WHEN** the usage summary is computed
- **THEN** requests, token sums, cached sums, error rate, top error, and per-model cost MUST equal the legacy per-row Python summation over the same rows
- **AND** as the sole exception, tied top-error counts MUST resolve deterministically (highest count, then error code ascending) rather than by the legacy dict insertion order

#### Scenario: Request-log insert issues no post-commit refresh

- **WHEN** a request log row is persisted
- **THEN** the write MUST NOT re-select the row after commit
