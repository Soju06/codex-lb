# Upstream Metadata Specification

## Purpose
The service SHALL maintain upstream pricing and Codex version metadata with offline fallbacks and repair retained missing request costs.
## Requirements
### Requirement: Automatic pricing metadata with offline fallbacks
The service SHALL refresh validated OpenAI text-token pricing from models.dev's public JSON, supplemented by compatible LiteLLM tier data at startup and periodically, without performing network I/O during cost calculation. It SHALL retain valid prices across partial updates and outages using a last-good persistent cache and a generated bundled snapshot before legacy code defaults. Invalid, negative, non-finite, incomplete, or non-OpenAI entries SHALL NOT replace valid prices. Exact models and their dated snapshots SHALL resolve before broad legacy aliases. Unlisted GPT-5 minor families SHALL remain unpriced until recognized instead of inheriting the generic GPT-5 price. A newer bundled snapshot SHALL take precedence over an older persistent snapshot for overlapping models. Explicit Priority/Flex and long-context rates SHALL be honored when present, including complete tier-specific long-context rates without standard long-context rates. Every long-context rate group SHALL require a positive threshold. Runtime and code-generation catalog reads SHALL enforce a 16 MiB response limit before JSON parsing.

#### Scenario: New model and outage
- **WHEN** a refresh discovers GPT-6 Astra and a later refresh fails
- **THEN** subsequent requests continue using the validated Astra prices, including after restart

### Requirement: Safe missing-cost repair
The service SHALL automatically fill only NULL costs for retained subscription request logs with sufficient token usage and recognized pricing. It SHALL skip external model-source logs and preserve non-NULL costs, including zero. Repairs SHALL execute in bounded transactions under the fold-state lock and mirror the exact cost deltas into lifetime account/key, hourly usage, quarter-hour demand, and report aggregates according to their existing filters, deduplication, dimensions, and watermark boundaries. A partial index SHALL support the bounded eligible-row scan, and PostgreSQL SHALL build that index concurrently with recovery of invalid interrupted builds. Repeated execution SHALL NOT double-count costs or rewrite request counts, token counts, or admission/limit counters. Unexpected refresh and backfill failures SHALL defer retries independently for five minutes while allowing the other operation to proceed.

#### Scenario: Folded history
- **WHEN** a retained Astra request with NULL cost has already been folded
- **THEN** its raw cost and each affected aggregate gain the same correction atomically
- **AND** pruned history and already priced rows remain unchanged

### Requirement: Automated Codex fallback metadata
The service SHALL persist successful stable Codex release resolutions across restarts, reject prereleases, accept stable release tags when display names are unavailable, and back off failed remote lookups while serving the last-good version or configured fallback. A repository command and daily workflow SHALL refresh the bundled stable version and pricing snapshot from public upstream data, with reviewable changes and failure on invalid source data. The daily workflow SHALL regenerate the settings reference and expose repository write credentials only to its publishing step. Previously resolved versions SHALL NOT be downgraded by stale remote data, and persisted versions below the configured fallback SHALL NOT override it.

#### Scenario: Offline restart
- **WHEN** GitHub and npm are unavailable after a successful version resolution and restart
- **THEN** the service uses the persisted stable version without repeated immediate retries

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
separately from cached-read tokens in proxy, automation compact, limit
warm-up and quota planner warm-up request logs. Quota planner API-key
reservations MUST retain the count at finalization. Known writes MUST replace ordinary input tokens at the
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

#### Scenario: Background native requests with writes

- **GIVEN** an automation compact or warm-up response reports cache-write tokens
- **WHEN** the request log is persisted
- **THEN** the upstream write count and corresponding write-rate cost are preserved
- **AND** a quota planner keyed warm-up finalizes its reservation with the count

#### Scenario: Background native requests without writes

- **GIVEN** a native background response does not report cache-write tokens
- **WHEN** its request log is persisted
- **THEN** no write usage is invented
