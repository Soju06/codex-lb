# query-caching Specification

## Purpose

Define query caching and quota-key normalization contracts so selection and dashboard reads remain fast and consistent.
## Requirements
### Requirement: Additional usage persistence normalizes upstream aliases to canonical quota keys
Persisted additional-usage rows MUST record one internal canonical `quota_key` even when upstream changes raw `limit_name` or `metered_feature` aliases.

#### Scenario: Legacy stored quota keys remain readable under the current canonical key
- **GIVEN** the registry renames a canonical additional-usage `quota_key`
- **AND** it lists the previous durable key as a legacy `quota_key` alias for that same quota family
- **WHEN** selection, dashboard, or cleanup code reads or deletes persisted rows for the current canonical key
- **THEN** rows stored under the legacy `quota_key` remain readable through the current canonical key
- **AND** canonical list/read results surface the current key instead of the legacy durable alias

#### Scenario: Refresh coalesces mixed aliases for one canonical quota before pruning
- **GIVEN** one refresh payload includes multiple `additional_rate_limits` items that resolve to the same canonical `quota_key`
- **AND** at least one alias reports usable window data while another alias for that same `quota_key` reports `rate_limit = null`
- **WHEN** the refresh persists additional usage
- **THEN** it merges all aliases by canonical `quota_key` before deleting stale rows
- **AND** persisted rows for the usable window remain available for later gated-model selection

#### Scenario: Historical rows remain readable after canonical key rename
- **GIVEN** persisted `additional_usage_history` rows were written under an earlier canonical `quota_key`
- **AND** the current registry still recognizes the same raw upstream aliases for that quota family
- **WHEN** selection or dashboard queries request the current canonical `quota_key`
- **THEN** repository reads match both the current `quota_key` and the known raw alias fields
- **AND** the historical rows remain visible until refresh rewrites them under the newer canonical key

### Requirement: Hot-path quota and dashboard aggregate reads avoid window-ranking scans
Selector and dashboard hot-path reads MUST avoid unbounded SQL window-ranking over `additional_usage_history` and `request_logs`; they MUST preserve existing result semantics. On PostgreSQL the additional-usage latest-per-account read MUST be served by correlated per-account top-1 index probes whose cost scales with the candidate account count and registry match values, not with the number of history rows stored under the quota key; grouped latest-id shapes remain acceptable for other reads and backends.

#### Scenario: Additional quota latest lookup avoids window ranking
- **GIVEN** multiple additional quota rows exist for each account under the same quota key and window
- **WHEN** gated-model selection loads the latest additional quota rows for candidate accounts
- **THEN** the query MUST NOT use `row_number()` or another full partition window-ranking expression
- **AND** on PostgreSQL the lookup MUST resolve each account through a top-1 probe ordered by `recorded_at DESC, used_percent DESC, id DESC` under an equality prefix on the match value, `window`, and account id
- **AND** the selected row per account MUST remain the newest `recorded_at`, then highest `used_percent`, then highest `id`

#### Scenario: Alias matches merge without a second history scan
- **GIVEN** the additional-quota registry declares `limit_name` or `metered_feature` aliases for a canonical quota key
- **AND** history rows exist that match only through an alias
- **WHEN** the latest additional quota rows are loaded for candidate accounts on PostgreSQL
- **THEN** alias matches MUST be resolved through per-account top-1 probes over expression indexes on the lowercased alias columns
- **AND** the merged winner per account MUST equal the newest row across canonical and alias matches under the `recorded_at DESC, used_percent DESC, id DESC` ordering

#### Scenario: Account request usage summary avoids request-log window ranking
- **GIVEN** dashboard account summaries aggregate request log usage per account
- **WHEN** account request usage summaries are loaded
- **THEN** the query MUST NOT rank the full `request_logs` set with `row_number()`
- **AND** duplicate request-log rows for the same account, request id, and requested timestamp MUST still collapse to the latest row id before aggregation

#### Scenario: Unfiltered distinct label listing avoids a full history pass
- **GIVEN** additional-usage history holds many rows spread over a small set of distinct `(quota_key, limit_name, metered_feature)` labels
- **WHEN** the distinct label listing is requested on PostgreSQL without a recency bound
- **THEN** the read MUST iterate distinct `(account_id, quota_key, limit_name, metered_feature)` tuples via ordered index probes instead of scanning every history row
- **AND** the canonicalized result set MUST equal the plain `DISTINCT` read

#### Scenario: Hot-path indexes are idempotent
- **GIVEN** a production database may already have manually-created hot-path indexes
- **WHEN** the schema migration for dashboard query hot paths is applied
- **THEN** the migration MUST complete without duplicate-index failure

### Requirement: Dashboard overview memoizes per-account depletion EWMA state

`GET /api/dashboard/overview` MUST cache per-account EWMA depletion state in memory so repeated polls do not re-walk the full in-window `usage_history` slice in the depletion cache check when its content is unchanged. SQLite bulk history cache hits MUST avoid rebuilding or materializing the full cached history window when compact digest metadata proves older rows are unchanged; they MUST append newly inserted rows by monotonic row ID and reuse the cached grouped history for older rows. Repository-owned mutations that reassign or delete usage-history rows MUST clear the SQLite bulk history cache.

#### Scenario: Repeated polls with unchanged history reuse cached EWMA state
- **GIVEN** the dashboard service has previously computed depletion for an account
- **AND** a subsequent request supplies the same in-window history slice for that account with the same attached compact content signature
- **WHEN** depletion is recomputed for the dashboard response
- **THEN** the service MUST reuse the cached EWMA state for that account instead of replaying every history row
- **AND** the depletion metrics for that account MUST match the previously returned values for rate-bearing fields
- **AND** the cache hit check MUST use bounded signature metadata rather than building or retaining a per-row signature tuple
- **AND** the service MUST prune cached depletion state for account/window keys that are absent from the current dashboard history set

#### Scenario: Memoized EWMA state is invalidated when a new usage row is appended
- **WHEN** a later dashboard request supplies the same account's in-window history with an additional row appended (a new `recorded_at` past the previous latest)
- **THEN** the service MUST rebuild the EWMA state from the new history slice
- **AND** the recomputed rate MUST reflect the newly observed sample

#### Scenario: Memoized EWMA state is invalidated when an older row ages out of the window
- **WHEN** a later dashboard request supplies the same account's in-window history with the earliest row dropped (because it has aged past the window cutoff)
- **THEN** the service MUST rebuild the EWMA state from the narrowed history slice
- **AND** the cached state from the wider window MUST NOT influence the recomputed rate

#### Scenario: Memoized EWMA state is invalidated when an existing usage row is corrected
- **WHEN** a later dashboard request supplies the same account's in-window history with the same row count and endpoints but a corrected `used_percent`, `reset_at`, or `window_minutes` value on an existing row
- **THEN** the service MUST rebuild the EWMA state from the corrected history slice
- **AND** the recomputed rate-bearing metrics MUST reflect the corrected row content

#### Scenario: SQLite bulk history cache hit appends only new rows
- **GIVEN** a SQLite bulk usage-history query has already cached rows for an account/window set
- **WHEN** a later query uses a narrower `since` timestamp and the database only has new rows with IDs greater than the cached max ID
- **THEN** the repository fetches the new rows and appends them to the cached grouped history
- **AND** it does not materialize the older cached rows as snapshots when compact digest metadata proves they are unchanged

#### Scenario: Usage-history ownership mutation clears SQLite bulk history cache
- **WHEN** an account merge or delete operation updates or deletes `usage_history` rows
- **THEN** the repository clears the SQLite bulk history cache before serving future cached dashboard history reads

### Requirement: Selector retry hint is bounded by the auto-recovery window

When `select_account` cannot return a candidate, the surfaced `"Try again in {N}s"` value MUST be clamped to at most `SELECTOR_RETRY_HINT_MAX_SECONDS` (default 300). Clients reattempt within codex-lb's auto-recovery window (background `/wham/usage` refresh + per-status cooldown threshold) instead of waiting the worst-case persisted `reset_at`. The clamp affects only the user-visible string; `AccountState.reset_at` and `AccountState.cooldown_until` remain unchanged and continue to drive selection, telemetry, and dashboard reads.

#### Scenario: Quota-exceeded reset far in the future is clamped
- **GIVEN** every selectable account has `status = QUOTA_EXCEEDED`
- **AND** the soonest `reset_at` is more than `SELECTOR_RETRY_HINT_MAX_SECONDS` from now
- **WHEN** `select_account` returns `account = None`
- **THEN** the surfaced message ends with `Try again in 300s`
- **AND** the underlying `AccountState.reset_at` values are unchanged

#### Scenario: Quota-exceeded reset inside the cap surfaces the actual value
- **GIVEN** every selectable account has `status = QUOTA_EXCEEDED`
- **AND** the soonest `reset_at` is at most `SELECTOR_RETRY_HINT_MAX_SECONDS` from now
- **WHEN** `select_account` returns `account = None`
- **THEN** the surfaced message ends with `Try again in {soonest_reset_seconds}s`

#### Scenario: Cooldown_until far in the future is clamped
- **GIVEN** every account has a `cooldown_until` further than `SELECTOR_RETRY_HINT_MAX_SECONDS` from now and no `quota_exceeded` candidates exist
- **WHEN** `select_account` returns `account = None`
- **THEN** the surfaced message ends with `Try again in 300s`

### Requirement: Gated model selection keeps requested quota windows isolated
When a request targets a gated model whose canonical additional quota is known, account selection SHALL rank and budget candidates using persisted usage windows for that requested additional quota only. Missing requested additional-quota windows SHALL NOT fall back to ordinary account usage windows for requested-limit ranking, budget-safety checks, or relative-availability scoring.

#### Scenario: Missing requested secondary window does not borrow ordinary secondary usage
- **GIVEN** account A has requested additional primary usage but no requested additional secondary usage
- **AND** account A has ordinary secondary usage near exhaustion
- **AND** account B has worse requested additional primary usage
- **WHEN** selecting an account for the gated model with requested-limit routing
- **THEN** account A is not penalized by its ordinary secondary usage for requested-limit ranking

#### Scenario: Requested secondary window is used when present
- **GIVEN** an account has requested additional primary and secondary usage windows
- **WHEN** selecting an account for the gated model with requested-limit routing
- **THEN** both requested additional windows may contribute to ranking and budget-safety decisions

#### Scenario: Requested reset window drives relative availability
- **GIVEN** account A has an ordinary secondary window that resets later than its requested additional quota
- **AND** account B has an ordinary secondary window that resets sooner than its requested additional quota
- **WHEN** selecting an account for the gated model with relative-availability routing
- **THEN** requested-limit scoring uses each account's requested additional-quota reset window instead of the ordinary secondary reset window

### Requirement: Quota status bypass preserves cooldown backoff
When requested additional-quota data proves an account can serve a gated model despite persisted `QUOTA_EXCEEDED` account status, account selection MAY bypass the persisted quota status for that requested gated model. This bypass SHALL NOT bypass `cooldown_until`, pause, deactivation, rate-limit, or error-backoff gates.

#### Scenario: Requested quota bypass does not bypass cooldown
- **GIVEN** an account is `QUOTA_EXCEEDED`
- **AND** requested additional-quota data marks the account eligible for the gated model
- **AND** the account has `cooldown_until` in the future
- **WHEN** selecting an account for that gated model
- **THEN** the account is not selected until the cooldown expires

#### Scenario: Requested quota bypass can select a cooled eligible account
- **GIVEN** an account is `QUOTA_EXCEEDED`
- **AND** requested additional-quota data marks the account eligible for the gated model
- **AND** the account has no active cooldown, pause, deactivation, rate-limit, or error backoff
- **WHEN** selecting an account for that gated model
- **THEN** the persisted quota status does not by itself exclude the account

### Requirement: OAuth account creation invalidates account and dashboard caches

After an OAuth flow successfully creates or refreshes an account, the SPA SHALL invalidate cached account and dashboard queries that surface account membership or account-derived dashboard data. The invalidation SHALL include the account list, account trend queries, dashboard overview, and dashboard projections.

The invalidation helper SHALL be reusable without importing account hook modules into OAuth hook tests.

#### Scenario: Manual browser OAuth success refreshes dashboard-visible account data

- **WHEN** a browser OAuth callback is submitted manually
- **AND** the OAuth callback response reports success
- **THEN** the SPA invalidates the account list query
- **AND** invalidates account trend queries
- **AND** invalidates the dashboard overview query
- **AND** invalidates the dashboard projections query

#### Scenario: Browser OAuth status success refreshes dashboard-visible account data

- **WHEN** a browser OAuth flow starts with a tracked flow id
- **AND** the OAuth status endpoint later reports success
- **THEN** the SPA invalidates the account list query
- **AND** invalidates account trend queries
- **AND** invalidates the dashboard overview query
- **AND** invalidates the dashboard projections query

#### Scenario: Device OAuth completion refreshes dashboard-visible account data

- **WHEN** a device-code OAuth completion request succeeds
- **THEN** the SPA invalidates the account list query
- **AND** invalidates account trend queries
- **AND** invalidates the dashboard overview query
- **AND** invalidates the dashboard projections query

#### Scenario: Failed OAuth does not refresh dashboard-visible account data

- **WHEN** an OAuth completion or callback request fails
- **THEN** the SPA does not invalidate account or dashboard queries for that failed OAuth attempt

### Requirement: Dashboard reads avoid hot-path full-history recomputation

The system SHALL keep dashboard hot-path database reads bounded by the data needed for the requested response whenever the existing API contract allows it. Dashboard query shapes MUST NOT combine a limited page fetch with an unbounded window aggregate that forces the database to materialize the entire filtered result before returning the page.

`GET /api/request-logs` MUST fetch request-log rows using a latest-first limited page query. If the response includes exact total metadata, the exact count MUST be computed using a separate count query or an equivalent cached/source-structured summary, not by adding `count(*) OVER()` to the paginated row query.

#### Scenario: Request-log page query does not materialize the full filtered result

- **GIVEN** the request-log table contains many rows matching the active filters
- **WHEN** the dashboard requests `GET /api/request-logs?limit=25&offset=0`
- **THEN** the row-fetch query returns only the requested page ordered by newest request first
- **AND** the row-fetch query does not include `count(*) OVER()` or an equivalent unbounded window aggregate
- **AND** the response still includes correct `total` and `hasMore` metadata

#### Scenario: Source-structured summaries remain available for broader dashboard optimization

- **GIVEN** a dashboard read repeatedly aggregates large raw histories such as request logs or usage history
- **WHEN** the aggregation cost dominates dashboard latency
- **THEN** the system MAY move that read to a cached, incremental, or source-structured summary so the dashboard does not repeatedly scan raw history on every poll
- **AND** the summary contract MUST preserve the externally visible dashboard semantics

### Requirement: Request-log listings filter by conversation ID

`GET /api/request-logs` MUST accept an optional `conversation_id` filter and
apply it together with every existing request-log filter, including timeframe,
status, model, account, API key, and search filters. The filter MUST use a bound
query parameter and MUST not change request routing or unrelated response data.

#### Scenario: Conversation-only filtering returns matching rows

- **GIVEN** request logs contain rows for `conv-a` and `conv-b`
- **WHEN** the request-log listing is requested with
  `conversation_id=conv-a`
- **THEN** only rows with conversation ID `conv-a` are returned

#### Scenario: Conversation filtering composes with existing filters

- **GIVEN** matching conversation rows differ by status, model, account, API
  key, timeframe, or search text
- **WHEN** a conversation filter and existing filters are requested together
- **THEN** every returned row matches the complete combined filter set

### Requirement: Filtered responses expose pagination-independent conversation aggregates

When `conversation_id` is present, the request-log response MUST include
`conversation.requestCount` and `conversation.aggregatedCostUsd`. Both values
MUST use the complete active filter set and MUST be independent of page limit and
offset. `aggregatedCostUsd` MUST sum stored `cost_usd` values, with no matches
represented as zero. The top-level listing total MUST remain consistent with the
filtered request count.

#### Scenario: Aggregates ignore pagination

- **GIVEN** a filtered conversation has twelve matching requests across multiple
  pages with a total stored cost of `1.23`
- **WHEN** page one and a later page are requested with different limit or
  offset values
- **THEN** both responses report `conversation.requestCount` as `12`
- **AND** both responses report `conversation.aggregatedCostUsd` as `1.23`

#### Scenario: No matching rows return zero aggregates

- **GIVEN** a conversation filter and active filters match no request logs
- **WHEN** the request-log listing is requested
- **THEN** the response reports `conversation.requestCount` as `0`
- **AND** the response reports `conversation.aggregatedCostUsd` as `0`

#### Scenario: No conversation filter returns null metadata

- **GIVEN** the request-log listing is requested without `conversation_id`
- **WHEN** the response is generated
- **THEN** the response's `conversation` metadata is null

### Requirement: Conversation filters participate in listing-count cache identity

The request-log listing-count cache signature MUST include `conversation_id` in
addition to every existing filter dimension. Requests for different
conversation IDs MUST not reuse one another's cached listing count.

#### Scenario: Different conversation IDs have isolated cached totals

- **GIVEN** two listing requests differ only by conversation ID
- **WHEN** their listing counts are served through the cache
- **THEN** each request uses its own cache entry and filtered total

### Requirement: Distinct conversation aggregates exclude null and blank IDs

Dashboard and report distinct-conversation aggregate queries MUST exclude null,
empty-string, and whitespace-only `conversation_id` values. SQL MUST use
`COUNT(DISTINCT NULLIF(TRIM(request_logs.conversation_id), ''))`, or an
equivalent database-specific expression with the same null-and-blank exclusion
semantics.

#### Scenario: Empty conversation IDs do not inflate aggregates

- **GIVEN** the active filtered range contains repeated `conv-a` values and
  rows whose conversation IDs are null, `''`, and `'   '`
- **WHEN** dashboard or report conversation aggregates are calculated
- **THEN** the distinct conversation count is `1`
- **AND** null and blank IDs do not create an unknown conversation bucket

### Requirement: Dashboard conversation trends aggregate by bucket

The dashboard conversation trend query MUST group by the configured time bucket
and count distinct non-empty normalized conversation IDs within each bucket. It
MUST exclude warmup traffic and MUST NOT use model or service-tier grouping that
could cause one conversation to be counted more than once in a bucket. For
hour-multiple display buckets the count MUST merge the conversation presence
rollup with the raw live tail through a UNION before the distinct count, so a
conversation appearing in both the folded segment and the raw tail of one
display bucket still counts once.

#### Scenario: One conversation across model groups counts once per bucket

- **GIVEN** a bucket contains two non-warmup request logs for `conv-a` under
  different models and one log for `conv-b`
- **WHEN** the dashboard conversation trend aggregate is calculated
- **THEN** that bucket's conversation count is `2`

#### Scenario: One conversation across the fold boundary counts once per bucket

- **GIVEN** a display bucket containing rows for `conv-a` below the
  conversation watermark (rollup-served) and above it (raw-served)
- **WHEN** the dashboard conversation trend aggregate is calculated
- **THEN** that bucket's conversation count counts `conv-a` once

### Requirement: Additional usage latest reads avoid SQLite window scans

Additional usage latest-per-account reads on SQLite MUST avoid `row_number()` window-function scans over the full `additional_usage_history` table. They MUST select matching accounts, then use indexed latest-row lookups ordered by `recorded_at DESC, used_percent DESC, id DESC` while preserving canonical quota-key and alias matching semantics. Non-SQLite dialects MAY keep the set-based window-function query.

#### Scenario: SQLite additional usage latest lookup uses indexed account probes
- **WHEN** additional usage latest rows are requested for a quota key, window, and optional account set on SQLite
- **THEN** the repository returns the same latest row per account as the set-based query
- **AND** the SQLite path does not emit a `row_number()` window-function query

### Requirement: Unfiltered request-log filter options avoid full DISTINCT passes

When `GET /api/request-logs/options` is requested without user-supplied filters, each facet (account ids, model/reasoning-effort pairs, api-key ids, status/error-code pairs) MUST be computed with loose-index-scan probes bounded by the facet's distinct-value count, not by the size of `request_logs`. The returned option sets, their ordering, and the soft-delete/status-facet semantics MUST be identical to the unbounded `DISTINCT` results.

#### Scenario: Unfiltered facets return identical option sets via bounded probes

- **GIVEN** request logs spanning multiple accounts, models with and without reasoning effort, api keys, and statuses with and without error codes
- **WHEN** the options endpoint is called with no filters
- **THEN** each facet MUST be produced by per-distinct-value index probes (recursive skip scan) rather than a full `DISTINCT` pass
- **AND** the response MUST equal the legacy `DISTINCT` results, including `(value, NULL)` pairs and ordering

#### Scenario: Soft-deleted rows stay excluded from skip-scanned facets

- **GIVEN** request-log rows with `deleted_at` set
- **WHEN** the options endpoint is called with no filters
- **THEN** values appearing only on soft-deleted rows MUST NOT appear in any facet

#### Scenario: Filtered requests keep bounded DISTINCT semantics

- **WHEN** the options endpoint is called with any user filter (`since`, `until`, account, api-key, model, or reasoning-effort constraints)
- **THEN** the facets MUST apply those filters with unchanged semantics and results

### Requirement: Proxy API-key auth caching is invalidation-driven with a TTL backstop

The proxy API-key auth cache MUST be invalidated through the cross-instance cache-invalidation mechanism on every key mutation (create/update/delete/reassignment), and its TTL MUST be at least 60 seconds so interactive request turns do not re-read unchanged key rows from the database.

#### Scenario: Key mutations invalidate cached auth promptly

- **GIVEN** an API key validated and cached on an instance
- **WHEN** the key is updated or deleted (on any instance)
- **THEN** the mutation MUST bump the api_key invalidation namespace
- **AND** cached auth data for that key MUST be cleared via the poller, independent of the TTL

#### Scenario: Unchanged keys are served from cache across interactive turns

- **GIVEN** a key validated less than 60 seconds ago with no intervening mutation
- **WHEN** another request authenticates with the same key
- **THEN** validation MUST be served from the cache without a database read

### Requirement: Sticky-session upsert completes in one statement

Sticky-session upserts on the request path MUST persist and return the row with a single `INSERT ... ON CONFLICT ... RETURNING` statement, with unchanged row contents and `updated_at` semantics.

#### Scenario: Upsert issues no follow-up selects

- **WHEN** a sticky session is created or re-affirmed
- **THEN** the repository MUST execute exactly one data statement (the returning upsert) plus the commit
- **AND** the returned row MUST reflect the persisted state for both the insert and update arms

### Requirement: Selection-input reads never run concurrently on a shared session

Account-selection input loading MUST NOT execute multiple statements concurrently on one `AsyncSession`.

#### Scenario: Usage window reads execute sequentially

- **WHEN** selection inputs load primary, secondary, and monthly usage windows
- **THEN** the three reads MUST be awaited sequentially on the shared session

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

### Requirement: Account request usage summaries combine a persistent rollup with a bounded live tail

Account request-usage summaries MUST NOT aggregate the full `request_logs` history per read. The read MUST combine persisted per-account rollup sums with a live aggregate constrained to rows newer than the rollup watermark, while preserving existing dedupe semantics (latest row id per `(account_id, request_id, requested_at)`) and existing filters (warmup kinds and soft-deleted rows excluded) on the live portion.

#### Scenario: Summary read does not scan folded history

- **GIVEN** rollup rows exist with watermark `folded_through = T`
- **WHEN** account request-usage summaries are loaded
- **THEN** the live request-log aggregate MUST constrain to `requested_at > T`
- **AND** the returned totals MUST equal the persisted rollup sums plus the live-tail aggregate per account
- **AND** the cached-input clamp (`cached_input_tokens ≤ input_tokens`) MUST apply to the merged totals

#### Scenario: Summary before the first fold matches legacy behavior

- **GIVEN** no rollup rows exist yet
- **WHEN** account request-usage summaries are loaded
- **THEN** the live aggregate MUST cover all non-deleted, non-warmup request-log history
- **AND** the returned totals MUST equal the pre-rollup query results

#### Scenario: Folding does not change reported totals

- **GIVEN** a set of request-log rows including duplicate rows sharing `(account_id, request_id, requested_at)`
- **WHEN** a fold pass folds part of that history and summaries are read afterwards
- **THEN** the totals MUST equal the totals the legacy full-history dedupe aggregate would report for the same rows

#### Scenario: Summary read is snapshot-consistent with a concurrent fold commit

- **GIVEN** a fold slice may commit at any point during a summary read
- **WHEN** the read fetches rollup sums and the watermark
- **THEN** both MUST come from a single database snapshot (one statement)
- **AND** no qualifying request-log row's contribution may be absent from both the rollup sums and the live-tail aggregate of that read

### Requirement: A background fold job advances the account usage rollup safely

A periodic background job MUST fold request-log rows into `account_usage_rollups` and advance the watermark. Folding MUST be restricted to rows older than a safety lag, MUST apply the dedupe and filtering semantics of the summary query within the folded window, MUST run on at most one instance at a time, and MUST be idempotent under repeated or concurrent invocation.

#### Scenario: Fold boundary respects the safety lag

- **WHEN** a fold pass runs at time `now`
- **THEN** it MUST NOT fold any row with `requested_at > now − lag`
- **AND** rows younger than the lag remain covered by the live-tail aggregate
- **AND** the lag MUST exceed the maximum possible request duration, because a log row is inserted at stream end but dated at request start and a row landing below the watermark would otherwise vanish from totals

#### Scenario: Duplicate rows never split across the fold boundary

- **GIVEN** duplicate request-log rows sharing the same `(account_id, request_id, requested_at)`
- **WHEN** a fold pass selects its window by `requested_at`
- **THEN** all rows of the duplicate group MUST land on the same side of the boundary
- **AND** only the latest row id of the group MUST contribute to the folded sums

#### Scenario: Fold is idempotent and single-writer

- **GIVEN** a fold pass has committed sums through watermark `T`
- **WHEN** another fold pass runs for the same window (repeat invocation or a second instance)
- **THEN** it MUST observe watermark `T` inside its transaction and fold no row at or before `T`
- **AND** no request-log row's contribution appears twice in the rollup

#### Scenario: Historical backfill is sliced and non-blocking

- **GIVEN** a deployment with existing request-log history and no rollup rows
- **WHEN** the first fold passes run
- **THEN** history MUST be folded in bounded time slices, each committed in its own transaction
- **AND** summary reads issued during backfill MUST return correct totals (rollup so far plus remaining live tail)

### Requirement: Account usage rollup rows follow the account lifecycle

Deleting an account MUST delete its rollup row in the same transaction as the account deletion, for both history-preserving and history-deleting variants. Consolidating duplicate accounts into a canonical account MUST transfer the duplicates' rollup sums to the canonical account in the same transaction that reassigns their request logs.

#### Scenario: Account deletion removes its rollup row

- **GIVEN** an account with a rollup row
- **WHEN** the account is deleted (with or without `delete_history`)
- **THEN** the rollup row MUST be deleted in the same transaction
- **AND** subsequent summaries MUST NOT report usage for that account

#### Scenario: Duplicate-account consolidation preserves folded usage

- **GIVEN** a canonical account and a duplicate account that both have folded rollup sums
- **WHEN** identity reconciliation consolidates the duplicate into the canonical account
- **THEN** the duplicate's rollup sums MUST be added to the canonical account's rollup row
- **AND** the duplicate's rollup row MUST be deleted in the same transaction
- **AND** the canonical account's summary MUST equal the combined pre-merge totals

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

### Requirement: Cross-replica cache invalidation bus bounds process-local cache staleness
Every process-local cache that serves security, authorization, or routing decisions MUST either register a namespace on the cross-replica cache-invalidation bus or declare a documented maximum cross-replica staleness TTL. Mutations MUST commit durable state before (or independently of) bumping their namespace version, each process MUST poll the `cache_invalidation` version table at a bounded interval (default 0.5s) and run registered namespace callbacks on version change, and the registered namespaces MUST include `api_key`, `firewall`, `account_routing`, `account_selection`, and `settings`. Each process MUST seed its baseline namespace versions before loading local caches / routing snapshots and before serving traffic, so a peer bump committed after that baseline is observed as a change (runs callbacks) rather than acknowledged as pre-existing state. A cache's TTL remains the fallback staleness bound when a bump is lost.

#### Scenario: Selection-state change on one replica converges on peers within the bus bound

- **GIVEN** two replicas share one database and each runs the cache-invalidation poller
- **AND** replica B holds warm cached selection inputs that include account X
- **WHEN** replica A persists a state change for account X and invalidates its selection cache with propagation
- **THEN** the `account_selection` namespace version is bumped within one poll cycle
- **AND** replica B's selection cache is invalidated on its next poll, without waiting for the cache TTL

#### Scenario: Peer bump committed before the first poll is not lost

- **GIVEN** a starting replica seeds its baseline namespace versions before loading its routing snapshot and before serving traffic
- **WHEN** a peer commits a mutation and bumps `account_routing` (or `settings`, or `account_selection`) after this replica loaded its caches but before its first poll cycle
- **THEN** the replica's first poll observes the bumped version as a change and runs the namespace callbacks
- **AND** the peer bump is not silently acknowledged as a baseline, so the cache is not left stale until the fallback TTL

#### Scenario: New security-relevant cache without bus coverage is a spec violation

- **GIVEN** a contributor adds a new process-local cache that gates a security, authorization, or routing decision
- **WHEN** the cache neither registers a cache-invalidation namespace nor documents a maximum cross-replica staleness TTL
- **THEN** the change violates this capability and is rejected at review

#### Scenario: Lost bump still converges within the fallback TTL

- **GIVEN** a mutation's namespace bump is permanently lost after retries
- **WHEN** peer replicas keep serving their cached values
- **THEN** each peer converges no later than that cache's documented fallback TTL

### Requirement: Cache invalidation bumps and polling are resilient and observable
`bump()` MUST retry transient write failures (including SQLite "database is locked") with a short backoff; on final failure it MUST log at ERROR with the namespace, increment `codex_lb_cache_invalidation_bump_failures_total{namespace}`, and MUST NOT fail the originating mutation. Coalesced (`request_bump`) namespaces MUST remain pending and be retried on subsequent poll cycles until a bump succeeds, and a `request_bump` arriving while a flush for the same namespace is already awaiting its bump MUST be preserved and produce a later bump. When any invalidation callback for a namespace fails, the poller MUST NOT acknowledge the observed version and MUST re-run that namespace's callbacks on subsequent poll cycles until they succeed. The poller MUST escalate consecutive poll failures above debug level after a bounded count (WARNING after 3, ERROR after 10) and increment `codex_lb_cache_invalidation_poll_failures_total`.

#### Scenario: Bump failure under database lock is observable and does not fail the mutation

- **GIVEN** the database rejects cache-invalidation writes with a lock error for longer than the retry budget
- **WHEN** a mutation attempts a durable namespace bump
- **THEN** the mutation itself still succeeds
- **AND** an ERROR log naming the namespace is emitted and the bump-failure counter increments

#### Scenario: Pending coalesced namespace flushes on the next successful cycle

- **GIVEN** a coalesced `request_bump` namespace failed to flush during a poll cycle
- **WHEN** the database becomes writable again
- **THEN** the next poll cycle flushes the pending namespace and increments its version

#### Scenario: Bump requested during an in-flight flush produces a later bump

- **GIVEN** a coalesced flush is awaiting the bump write for a namespace
- **WHEN** another mutation commits and requests a bump for the same namespace before the flush completes
- **THEN** the namespace is re-queued and flushed again on a subsequent cycle, incrementing the version beyond the in-flight bump

#### Scenario: Failed invalidation callback keeps the version unacknowledged and is retried

- **GIVEN** a replica observes an `account_routing` version bump
- **AND** its routing snapshot refresh fails with a transient database error
- **WHEN** the poll cycle completes
- **THEN** the replica does not record the new version as seen
- **AND** the refresh is retried on subsequent poll cycles until it succeeds

#### Scenario: Consecutive poll failures escalate above debug

- **GIVEN** a replica's poller cannot read the `cache_invalidation` table
- **WHEN** three consecutive polls fail
- **THEN** a WARNING is logged and the poll-failure counter increments

### Requirement: Projection history reads are bounded per account
The dashboard projections history fetch MUST NOT widen every account's
lookback to the widest account window. On PostgreSQL the bulk usage-history
read MUST bound rows per account by that account's own window cutoff; the
returned per-account histories MUST equal the previous shared-floor fetch
after the existing per-account trimming.

#### Scenario: One weekly account does not widen the fetch for short-window accounts
- **GIVEN** one account with a 7-day window and several accounts with 5-hour windows
- **WHEN** the projections history fetch runs on PostgreSQL
- **THEN** rows for the 5-hour accounts MUST be bounded by their own cutoff in SQL
- **AND** each account's resulting history slice MUST equal the slice the shared-floor fetch produced after per-account trimming

#### Scenario: SQLite snapshot cache keeps the shared floor
- **GIVEN** the SQLite backend serves the projections history fetch through its snapshot cache
- **WHEN** per-account cutoffs are supplied
- **THEN** the SQLite read MAY keep the shared floor
- **AND** per-account trimming in the caller MUST still bound each account's slice

### Requirement: Request-log listing totals are cached and rollup-served
The request-log listing total MUST be served from a short-TTL per-filter
cache. On a cache miss, filter signatures whose every active filter maps
onto a demand-rollup dimension (time bounds, accounts, api keys,
model/effort pairs, statuses, soft-delete exclusion) MUST be counted as the
demand rollup's folded `SUM(request_count)` under the hourly watermark plus
an exact raw count over the un-folded complement windows; the result MUST
equal the legacy raw `COUNT(*)`. Signatures carrying free-text search or
error-code splits MUST fall back to the exact raw count.

#### Scenario: Default listing total avoids a full history scan once folded
- **GIVEN** request logs folded below the hourly watermark and a live raw tail
- **WHEN** the listing total is computed with default filters
- **THEN** the folded portion MUST be one aggregated read over the demand rollup bounded by the watermark
- **AND** only the un-folded complement windows are counted from raw
- **AND** the total MUST equal the raw `COUNT(*)` over the same filters

#### Scenario: Status splits stay exact through the rollup
- **GIVEN** history containing success, error, and cancelled requests on both sides of the watermark
- **WHEN** the listing total is computed for the default success+error split, a single status, or no status filter
- **THEN** the rollup-served total MUST equal the raw count for the same split

#### Scenario: Non-expressible filters fall back to the raw count
- **GIVEN** a listing filtered by free-text search or an error-code split
- **WHEN** the total is computed
- **THEN** the exact raw `COUNT(*)` path MUST be used

#### Scenario: Retention pruning keeps totals aligned with listable rows
- **GIVEN** retention has pruned folded raw rows while their demand-rollup counts remain
- **WHEN** the listing total is computed for an expressible signature
- **THEN** the rollup window MUST be clamped to the earliest surviving live row
- **AND** the total MUST equal the raw count over the surviving rows, never advertising pages the listing cannot return

#### Scenario: Offset-aware time bounds are accepted
- **GIVEN** the dashboard sends ISO-8601 `Z` (offset-aware) `since`/`until` bounds
- **WHEN** the listing total is computed
- **THEN** the bounds MUST be normalized to the naive-UTC domain before window arithmetic
- **AND** the result MUST equal the naive-UTC equivalent request

#### Scenario: No watermark degrades to the legacy count
- **GIVEN** no hourly fold watermark exists (pre-backfill or after the operator escape hatch)
- **WHEN** the listing total is computed for an expressible signature
- **THEN** the folded sum MUST be empty and the raw windows MUST cover the full range
- **AND** the result MUST be the exact legacy count with no kill switch involved

### Requirement: A conversation presence rollup serves distinct-conversation reads

The system SHALL maintain a permanent conversation presence satellite `request_conversation_hourly_rollups` dimensioned by `(bucket_epoch, conversation_id, account_id, is_deleted)` with an additive `request_count` measure, folded from `request_logs` rows whose normalized conversation id (`NULLIF(TRIM(conversation_id), '')`) is non-null and whose `request_kind` is not a warmup kind. `conversation_id` MUST be stored as the normalized value; `is_deleted` MUST be a dimension (not a fold filter) because dashboard conversation reads exclude soft-deleted rows while reports conversation reads include them; `account_id` MUST be carried (NULL-sentinel encoded) solely so account lifecycle mirrors can re-attribute or remove folded presence exactly as the corresponding raw mutation does. The fold SHALL advance a dedicated hour-aligned `conversation_folded_through` watermark on `account_usage_rollup_state` under the shared fold-state row lock, with the established slice contract (DELETE-then-INSERT over half-open hour-aligned windows committed atomically with the watermark advance, bounded paced backfill, fold lag). Rollup rows MUST NOT be deleted by data retention.

#### Scenario: Conversation straddling the fold boundary counts once

- **GIVEN** one conversation with request rows both below and above `conversation_folded_through`
- **WHEN** a switched distinct-conversation read spans both sides
- **THEN** the conversation counts exactly once
- **AND** the additive conversation-request total equals the folded `request_count` sum plus the raw-tail row count

#### Scenario: Soft delete moves presence to the orphaned-deleted dimension

- **GIVEN** folded conversation presence attributed to an account
- **WHEN** the account is soft-deleted (raw history detached with `account_id=NULL, deleted_at=now`)
- **THEN** the folded presence moves to the NULL-sentinel, `is_deleted=true` dimension in the same transaction
- **AND** dashboard conversation reads stop counting it while reports conversation reads keep counting it

#### Scenario: Hard history delete removes only that account's presence

- **GIVEN** a conversation with folded presence from two accounts
- **WHEN** one account is deleted with history removal
- **THEN** only that account's presence rows are removed
- **AND** the conversation still counts through the surviving account's presence, matching a raw scan of the surviving rows

#### Scenario: Fold is idempotent

- **GIVEN** a completed conversation fold pass
- **WHEN** the pass re-runs with the same clock
- **THEN** it commits no slices and the satellite contents are unchanged

### Requirement: Distinct-conversation reads combine the presence rollup with a raw live tail in one statement

The dashboard conversation activity metrics (`conversation_count`, `conversation_request_count`), the dashboard conversation trend buckets, and the UNFILTERED reports summary and per-day conversation counts MUST serve folded history from the presence satellite and the remainder from raw `request_logs`, merged in a single statement per read: the fold watermark joined into both branches of a UNION so the folded segment, its exact raw complement, and the watermark come from one database snapshot, and `COUNT(DISTINCT ...)` deduplicates across the fold boundary. Merged results MUST equal the legacy full-raw aggregation whenever the underlying raw rows still exist. With an epoch or missing watermark the reads MUST degrade to exactly the legacy raw queries (no kill switch). Reports reads carrying account, model, or useragent filters MUST keep the legacy raw statement (the satellite has no such dimensions), and non-hour-multiple dashboard display buckets MUST keep the full-raw path. This reverses the `add-request-log-usage-rollups` non-goal that kept distinct conversation counts raw-bound: conversation statistics over folded history now survive request-log retention pruning, except the documented raw-bound residues (sub-hour window edges, filtered reports reads, and daily-report day-row membership, which stays raw-driven).

#### Scenario: Switched conversation reads equal legacy reads while raw exists

- **GIVEN** a corpus with conversations spanning hours, blank and NULL conversation ids, warmup kinds, and soft-deleted rows
- **WHEN** each switched conversation read runs with the conversation watermark at epoch, mid-history on an hour boundary, and at the fold target — including states where the hourly and conversation watermarks differ
- **THEN** every result equals the legacy raw-only implementation exactly

#### Scenario: Conversation statistics survive raw pruning

- **GIVEN** folded conversation presence whose source raw rows have been pruned by retention
- **WHEN** the dashboard conversation activity metrics, hour-multiple conversation trend buckets, or the unfiltered reports summary conversation count are read over that period
- **THEN** the distinct-conversation values equal those reported before the pruning (modulo the documented sub-bucket window edges)

#### Scenario: Filtered reports reads stay raw-bound

- **GIVEN** a reports summary or daily read filtered by account, model, or useragent group
- **WHEN** the read executes
- **THEN** it uses the legacy raw statement and reaches only as far back as raw retention keeps rows

#### Scenario: Non-hour-multiple conversation buckets degrade to full raw

- **GIVEN** a conversation trend request with a display bucket that is not a whole multiple of the rollup hour
- **WHEN** the aggregate is calculated
- **THEN** the legacy full-raw query is used unchanged

### Requirement: Projection history bulk reads are index-covered on PostgreSQL
The columns selected by the dashboard projections bulk usage-history fetch
MUST be fully covered by an index matching each of its predicate shapes on
PostgreSQL — the coalesced-primary window shape and the explicit raw-window
shape — so the read can be planned as an index-only scan without per-row
heap fetches. Non-PostgreSQL backends MUST keep the same-named indexes for
schema parity but MAY omit the covering payload.

#### Scenario: Primary-window bulk fetch plans as an index-only scan
- **GIVEN** usage history rows exist for multiple accounts with `NULL` and `'primary'` windows
- **AND** the table's visibility map is populated (`VACUUM ANALYZE` has run since the rows were written; with an empty visibility map the planner MAY prefer a plain Index Scan on a cheaper non-covering index)
- **WHEN** the bulk history fetch shape for `window="primary"` is EXPLAINed on PostgreSQL with sequential and bitmap scans disabled
- **THEN** the plan MUST be an Index Only Scan over the covering index whose keys are `(coalesce("window",'primary'), account_id, recorded_at)`
- **AND** the covering payload MUST carry the raw `"window"` column so the coalesce qual does not disqualify the index-only path
- **AND** the fetched rows MUST equal the non-covered read (the same fetch executed with index-only scans disabled), up to ordering among rows tied on the query's sort key

#### Scenario: Raw secondary-window bulk fetch plans as an index-only scan
- **GIVEN** usage history rows exist for multiple accounts with `'secondary'` windows
- **AND** the table's visibility map is populated (`VACUUM ANALYZE` has run since the rows were written)
- **WHEN** the bulk history fetch shape for `window="secondary"` is EXPLAINed on PostgreSQL with sequential and bitmap scans disabled
- **THEN** the plan MUST be an Index Only Scan over the covering index whose keys are `("window", account_id, recorded_at)`

#### Scenario: Covering indexes are created concurrently and repair invalid leftovers
- **GIVEN** a PostgreSQL database where a previous `CREATE INDEX CONCURRENTLY` for a covering index was interrupted and left an invalid index
- **WHEN** the covering-index migration is applied
- **THEN** the invalid leftover MUST be dropped and the index rebuilt concurrently
- **AND** re-running the migration MUST complete without duplicate-index failure

#### Scenario: Missing covering index fails schema drift checks
- **GIVEN** a database whose `usage_history` table lacks one of the covering indexes
- **WHEN** the schema drift check runs
- **THEN** it MUST report the missing index by name

### Requirement: Append-heavy usage-history visibility is maintained for the covering path
Covering indexes alone do not keep the bulk read heap-free: `usage_history`
is append-heavy (high-frequency inserts, no updates or deletes), and with
PostgreSQL's default insert-driven autovacuum trigger the freshly appended
pages stay outside the visibility map long enough that "index-only" scans
degrade into per-row heap fetches. The `usage_history` table on PostgreSQL
MUST therefore carry per-table insert-driven autovacuum tuning
(`autovacuum_vacuum_insert_scale_factor = 0.02`,
`autovacuum_vacuum_insert_threshold = 50000`,
`autovacuum_analyze_scale_factor = 0.02`, matching the tuning already
applied to the other insert-heavy tables by `20260717_000000`) so the
visibility map stays fresh and the covering read path remains index-only.
Non-PostgreSQL backends MUST NOT be affected (no visibility map).

#### Scenario: Migration sets the insert-driven autovacuum parameters
- **GIVEN** a PostgreSQL database migrated past the covering-index revision
- **WHEN** the autovacuum tuning revision is applied
- **THEN** `usage_history` reloptions MUST include `autovacuum_vacuum_insert_scale_factor=0.02`, `autovacuum_vacuum_insert_threshold=50000`, and `autovacuum_analyze_scale_factor=0.02`
- **AND** downgrading the revision MUST reset those three parameters

#### Scenario: Re-applying over a manually tuned deployment is harmless
- **GIVEN** a PostgreSQL deployment where the identical autovacuum settings were already applied manually (the reference deployment's hotfix)
- **WHEN** the autovacuum tuning revision is applied
- **THEN** the migration MUST complete without error and leave the same settings in place

### Requirement: Request-log listing totals are cached per filter signature

The request-log listing MUST NOT execute an exact `COUNT(*)` over the filtered set on every page request; the total MUST be reused from a per-filter-signature cache within a fixed 30-second TTL (an application constant per the `reduce-settings-surface-phase-2` change — not an operator tunable). Cached totals are display-only: page contents themselves MUST remain exact and newest-first.

#### Scenario: Repeated pages reuse the cached total

- **GIVEN** two listing requests with the same filters but different offsets within the TTL
- **WHEN** both pages are served
- **THEN** the filtered set is counted once and both responses report the same total

#### Scenario: Distinct filter signatures count independently

- **WHEN** a listing request arrives with different filters
- **THEN** its total comes from its own count, not another signature's cache entry

#### Scenario: Expired entries are recounted

- **GIVEN** a cached total whose 30-second TTL has elapsed
- **WHEN** a listing request with the same filter signature arrives
- **THEN** an exact count is executed and the cache entry is refreshed

### Requirement: Upstream-route resolution is invalidation-driven with a TTL backstop

Proxy hot-path upstream-route resolution MUST be served from a per-account cache of resolver outcomes. Admin mutations of any resolver input (account proxy bindings, proxy pool membership, upstream-proxy dashboard settings, account deletion cascading a binding away) MUST invalidate the cache on the mutating replica before the mutating response returns and durably bump a cache-invalidation namespace so peer replicas converge within one poll interval. If the durable bump write fails (the bump primitive is non-raising), the implementation MUST enqueue the coalesced retry so peers still converge on the first poll cycle after the write path recovers. The cache TTL MUST default to 60 seconds as a backstop for out-of-band database edits, and a TTL of 0 MUST disable caching entirely.

#### Scenario: Repeat turns skip route re-resolution

- **GIVEN** an account whose route resolved less than the TTL ago with no intervening route-input mutation
- **WHEN** another proxy request uses that account
- **THEN** the route MUST be served from the cache without opening a database session

#### Scenario: Binding change invalidates before the response returns

- **GIVEN** a cached route outcome for an account
- **WHEN** an operator upserts that account's proxy binding
- **THEN** the mutating replica's cache MUST be cleared before the HTTP response returns
- **AND** the `upstream_route` namespace MUST be durably bumped so peers clear their caches via the poller

#### Scenario: Pool membership change invalidates

- **GIVEN** a cached route outcome resolved from a pool
- **WHEN** an operator adds a member to any proxy pool
- **THEN** the local cache MUST be cleared and the `upstream_route` namespace durably bumped before the response returns

#### Scenario: Account deletion invalidates

- **GIVEN** a cached route outcome for an account
- **WHEN** an operator deletes the account (cascading its proxy binding away)
- **THEN** the local cache MUST be cleared and the `upstream_route` namespace durably bumped before the response returns

#### Scenario: Peer replicas converge through the poller

- **GIVEN** a cached route outcome on a replica that did not perform the mutation
- **WHEN** the `upstream_route` or `settings` namespace version advances
- **THEN** that replica's cache-invalidation poller MUST clear its route cache within one poll interval

#### Scenario: Upstream settings change invalidates

- **GIVEN** a cached route outcome
- **WHEN** an operator changes `upstream_proxy_routing_enabled` or `upstream_proxy_default_pool_id`
- **THEN** the mutating replica's route cache MUST be cleared and the `upstream_route` namespace durably bumped (with the coalesced retry on write failure) before the response returns
- **AND** peers MUST also clear theirs via the durable `settings` namespace bump

### Requirement: Aggregated rate-limit reads never run concurrently on a shared session

Proxy rate-limit header and usage-payload construction MUST NOT execute
multiple statements concurrently on one `AsyncSession`. Repository objects
exposed by the same `ProxyRepositories` context SHALL be treated as sharing that
single-session ownership constraint.

#### Scenario: Rate-limit header reads execute sequentially

- **WHEN** the proxy constructs upstream-quota rate-limit headers from primary, secondary, monthly, and credit usage rows
- **THEN** each database read MUST complete before the next read starts on the shared session
- **AND** the returned header names and values remain unchanged for equivalent rows

#### Scenario: Codex usage payload reads execute sequentially

- **WHEN** the proxy constructs the aggregate `/api/codex/usage` payload for a request that does not resolve to a codex-lb API key, using usage windows, credits, and additional limits
- **THEN** each database read MUST complete before the next read starts on the shared session
- **AND** the returned payload remains schema- and value-compatible for equivalent rows

