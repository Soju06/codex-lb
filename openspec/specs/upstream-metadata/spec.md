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
