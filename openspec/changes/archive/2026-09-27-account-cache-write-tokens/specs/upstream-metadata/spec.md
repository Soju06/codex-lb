## ADDED Requirements

### Requirement: Explicit cache-write model prices

The pricing catalog MUST preserve cache-write rates supplied by its supported
sources for standard, priority and flex service tiers and supported long-context
groups. Cache-write rates MUST use the same model identity and group-coherence
rules as the associated input rates.

#### Scenario: Source includes tier and context write prices

- **GIVEN** a supported model-price source includes cache-write rates
- **WHEN** the pricing catalog is refreshed or restored from its cache
- **THEN** applicable write rates remain available to cost calculation
- **AND** ordinary input, cached-read and output rates retain their existing behavior

#### Scenario: Model has no separate write price

- **GIVEN** a model has no separate cache-write price
- **WHEN** its usage is priced
- **THEN** input without known separately priced writes retains ordinary input pricing

### Requirement: Disjoint cache-write input accounting

Native Codex request accounting MUST preserve an upstream cache-write token count
separately from cached-read tokens. Known writes MUST replace ordinary input tokens at the
applicable write price, not incur both prices. The sum of billed input categories
MUST NOT exceed normalized input tokens. Writes MUST be clamped to the nonnegative
input remainder after existing cached-read normalization.

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
