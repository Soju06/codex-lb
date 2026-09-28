## MODIFIED Requirements

### Requirement: Disjoint cache-write input accounting

Native Codex request accounting MUST preserve an upstream cache-write token
count separately from cached-read tokens in proxy, automation compact, limit
warm-up and quota planner warm-up request logs. Quota planner API-key
reservations MUST retain the count at finalization. Known writes MUST replace
ordinary input tokens at the applicable write price, not incur both prices.
The sum of billed input categories MUST NOT exceed normalized input tokens.
Writes MUST be clamped to the nonnegative input remainder after existing
cached-read normalization.

#### Scenario: Standard Astra cache write

- **GIVEN** input usage contains 100000 cache-write tokens, no reads or output
- **AND** ordinary input costs 10 USD and writes cost 12.5 USD per million tokens
- **WHEN** a successful request is logged
- **THEN** its recorded cost is 1.25 USD rather than 1.00 or 2.25 USD
- **AND** read-side totals include that recorded cost

#### Scenario: Missing or excessive write counts

- **GIVEN** a write count is absent, zero, negative or larger than remaining input
- **WHEN** usage is normalized
- **THEN** absent or negative writes count as zero
- **AND** excessive writes are limited to input not already counted as cached reads

#### Scenario: Persisted write usage and historical rows

- **GIVEN** request logs exist before the write-count migration
- **WHEN** the schema is upgraded
- **THEN** historical write counts remain unknown without invented backfill
- **AND** existing non-NULL costs remain unchanged
- **AND** new write counts survive persistence and missing-cost reconstruction

#### Scenario: Background native requests with writes

- **GIVEN** an automation compact or warm-up response reports cache-write tokens
- **WHEN** the request log is persisted
- **THEN** the upstream write count and corresponding write-rate cost are preserved
- **AND** a quota planner keyed warm-up finalizes its reservation with the count

#### Scenario: Background native requests without writes

- **GIVEN** a native background response does not report cache-write tokens
- **WHEN** its request log is persisted
- **THEN** no write usage is invented
