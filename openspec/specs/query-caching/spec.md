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
Selector and dashboard hot-path reads MUST avoid unbounded SQL window-ranking over `additional_usage_history` and `request_logs`; they MUST preserve existing result semantics while using grouped latest-id or `DISTINCT ON` shapes plus supporting indexes.

#### Scenario: Additional quota latest lookup avoids window ranking
- **GIVEN** multiple additional quota rows exist for each account under the same quota key and window
- **WHEN** gated-model selection loads the latest additional quota rows for candidate accounts
- **THEN** the query MUST NOT use `row_number()` or another full partition window-ranking expression
- **AND** the hot-path lookup MUST constrain by canonical `quota_key`, `window`, and candidate account ids so the latest-row index remains usable
- **AND** the selected row per account MUST remain the newest `recorded_at`, then highest `used_percent`, then highest `id`

#### Scenario: Account request usage summary avoids request-log window ranking
- **GIVEN** dashboard account summaries aggregate request log usage per account
- **WHEN** account request usage summaries are loaded
- **THEN** the query MUST NOT rank the full `request_logs` set with `row_number()`
- **AND** duplicate request-log rows for the same account, request id, and requested timestamp MUST still collapse to the latest row id before aggregation

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

### Requirement: Additional usage latest reads avoid SQLite window scans

Additional usage latest-per-account reads on SQLite MUST avoid `row_number()` window-function scans over the full `additional_usage_history` table. They MUST select matching accounts, then use indexed latest-row lookups ordered by `recorded_at DESC, used_percent DESC, id DESC` while preserving canonical quota-key and alias matching semantics. Non-SQLite dialects MAY keep the set-based window-function query.

#### Scenario: SQLite additional usage latest lookup uses indexed account probes
- **WHEN** additional usage latest rows are requested for a quota key, window, and optional account set on SQLite
- **THEN** the repository returns the same latest row per account as the set-based query
- **AND** the SQLite path does not emit a `row_number()` window-function query

