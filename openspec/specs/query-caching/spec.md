# query-caching Specification

## Purpose
TBD - created by syncing change query-optimization-and-caching. Update Purpose after archive.

## Requirements
### Requirement: Rate limit headers cache
The system MUST cache rate-limit header calculations on the proxy request path with a TTL aligned to the usage refresh interval, and it MUST invalidate that cache immediately when a usage refresh cycle completes.

#### Scenario: Cached headers are reused within the TTL
- **WHEN** a proxy request arrives while rate-limit headers are already cached within the TTL
- **THEN** the system returns the cached headers without running DB queries (SHALL)

#### Scenario: Usage refresh invalidates the cache
- **WHEN** the background usage refresh scheduler completes a refresh cycle
- **THEN** the rate-limit header cache is invalidated so the next request recomputes headers from fresh data (SHALL)

#### Scenario: Cache miss recomputes from the DB
- **WHEN** a proxy request arrives after the cache is empty or expired
- **THEN** the system reads rate-limit data from the DB, computes the headers, and stores the result in the cache (SHALL)

### Requirement: Settings 캐시 활용
The proxy request path MUST read dashboard settings through `SettingsCache` instead of opening a separate DB session.

#### Scenario: Proxy requests use the settings cache
- **WHEN** a stream or compact proxy request needs settings such as `sticky_threads_enabled` or `prefer_earlier_reset_accounts`
- **THEN** the system reads them from `SettingsCache` and does not create a separate DB session (SHALL)

### Requirement: 계정 선택 시 중복 쿼리 제거
`LoadBalancer.select_account()` MUST skip redundant `latest_by_account()` calls when no usage refresh actually ran.

#### Scenario: Existing usage data is reused when nothing was refreshed
- **WHEN** `refresh_accounts()` skips every account and no actual refresh occurs
- **THEN** the system reuses the previously loaded `latest_by_account()` result and does not run an additional query (SHALL)

#### Scenario: Latest data is re-read after a refresh
- **WHEN** `refresh_accounts()` updates usage for one or more accounts
- **THEN** the system calls `latest_by_account()` again so the refreshed data is reflected (SHALL)

### Requirement: Account selection avoids refresh-driven head-of-line blocking
`LoadBalancer.select_account()` MUST NOT hold the in-memory runtime lock while it performs pre-selection usage refresh work.

#### Scenario: Runtime mutations proceed while refresh is in flight
- **WHEN** one request is refreshing usage data before account selection
- **THEN** concurrent runtime mutations such as `record_error()` are not blocked waiting for that refresh to finish (SHALL)

#### Scenario: Final account selection remains serialized
- **WHEN** concurrent requests complete any pre-selection refresh work and attempt to choose accounts
- **THEN** the final in-memory selection and runtime-state update step still runs under the runtime lock (SHALL)

### Requirement: latest_by_account 쿼리 효율화
`usage_history` latest-row lookups MUST filter at the DB level instead of loading the full table into Python.

#### Scenario: Only the latest row per account is returned
- **WHEN** `latest_by_account(window)` is called
- **THEN** the system uses a SQL subquery to fetch only the latest row per account and does not load the full row set into Python (SHALL)
- **AND** the result shape remains `dict[str, UsageHistory]` (SHALL)

### Requirement: Primary window reads use normalized DB predicates
Primary-window reads against `usage_history` MUST use a normalized database predicate that treats legacy `NULL` rows and `"primary"` rows as the same logical window while remaining index-friendly.

#### Scenario: Primary window query preserves legacy NULL semantics
- **WHEN** `latest_by_account("primary")`, `aggregate_since(..., window="primary")`, or `latest_window_minutes("primary")` is called
- **THEN** rows with `window IS NULL` and rows with `window = "primary"` are treated as the same primary window (SHALL)
- **AND** rows for other windows such as `secondary` are excluded (SHALL)

#### Scenario: Primary window query remains DB-index friendly
- **WHEN** the primary-window latest-row query runs
- **THEN** it uses an index-compatible normalized predicate based on `coalesce(window, 'primary')` and a matching composite index (SHALL)

### Requirement: Request logs list and count use a single query
Request-log listing MUST combine the row query and total-count query into a single query.

#### Scenario: Pagination returns rows and total together
- **WHEN** the request logs list API is called
- **THEN** the system returns rows and total count from a single query using a window function (SHALL)
- **AND** the API response shape remains `requests`, `total`, and `has_more` (SHALL)

### Requirement: Request logs list query avoids unnecessary related-table joins
Default `request_logs` list/count queries MUST join related tables only when the request needs account-email or API-key-name search behavior.

#### Scenario: Non-search request avoids related-table joins
- **WHEN** the request logs list API is called without `search`
- **THEN** it computes rows and total count from `request_logs` without joining `accounts` or `api_keys` (SHALL)

#### Scenario: Search keeps email and API key name matching
- **WHEN** the request logs list API is called with `search`
- **THEN** matching against account email and API key name remains supported exactly as before (SHALL)

### Requirement: Refreshed flag accuracy
The `refreshed` value returned by `refresh_accounts()` MUST represent only whether `usage_history` was actually changed. `_refresh_account()` MUST return `AccountRefreshResult(usage_written: bool)`.

#### Scenario: No DB write means refreshed stays false
- **WHEN** `_refresh_account()` exits without writing to the DB because there is no payload, no rate-limit information, or an API error
- **THEN** `refresh_accounts()` does not set `refreshed=True` for that account (SHALL)

#### Scenario: Writing usage rows sets refreshed true
- **WHEN** `_refresh_account()` creates one or more primary or secondary usage rows
- **THEN** `refresh_accounts()` returns `refreshed=True` (SHALL)

#### Scenario: Refreshed false suppresses the re-read
- **WHEN** `LoadBalancer.select_account()` sees `refreshed=False`
- **THEN** it reuses the existing `latest_by_account()` result instead of issuing another query (SHALL)

### Requirement: API key quota validation is atomic
API key quota validation and deduction or reservation MUST be handled through a single atomic operation so concurrent requests cannot overrun quota by passing a stale counter check.

#### Scenario: Atomic reservation succeeds
- **WHEN** quota remains available and `try_reserve_usage()` is called
- **THEN** the system atomically increments `current_value` and returns success (SHALL)
- **AND** it guarantees concurrency with a conditional update/CAS pattern (SHALL)

#### Scenario: Atomic reservation failure returns 429
- **WHEN** `current_value + delta > max_value`
- **THEN** the update affects zero rows and the service returns HTTP 429 (SHALL)

#### Scenario: Parallel requests do not exceed quota
- **WHEN** N parallel requests target a key that is close to its limit
- **THEN** the sum of accepted requests does not exceed the quota (SHALL)

#### Scenario: Upstream failure releases the reservation
- **WHEN** reservation succeeds and the upstream request later fails
- **THEN** the reserved usage is rolled back (SHALL)

#### Scenario: Finalize is idempotent
- **WHEN** `finalize` is called multiple times with the same `usage_reservation_id`
- **THEN** usage is applied only once (SHALL)

### Requirement: TOTP disable requires a fully stepped-up session
`disable_totp()` MUST require a fully authenticated session with both `password_verified=true` and `totp_verified=true`. A password-only verified session must not be allowed to disable TOTP.

#### Scenario: Incomplete authentication is rejected
- **WHEN** `disable_totp()` is called from a session with `password_verified=True` and `totp_verified=False`
- **THEN** the request is rejected (SHALL)

#### Scenario: Fully authenticated session may disable TOTP
- **WHEN** `disable_totp()` is called from a session with `password_verified=True` and `totp_verified=True`
- **THEN** the TOTP disable operation is processed (SHALL)

#### Scenario: Replayed TOTP codes are rejected
- **WHEN** the `disable_totp()` path requires TOTP code revalidation
- **AND** the submitted code belongs to a TOTP step that was already used
- **THEN** the disable operation fails (SHALL)

### Requirement: Stale selected filter values remain visible
`MultiSelectFilter` MUST continue to render selected values that have disappeared from the latest server options and allow users to clear them individually.

#### Scenario: Stale values are shown and removable
- **WHEN** a selected value disappears from the latest `options`
- **THEN** the UI continues to render that value with a stale indicator (SHALL)
- **AND** the user can clear it individually (SHALL)

#### Scenario: Clearing a stale value updates the results
- **WHEN** a stale selected value is cleared
- **THEN** the system removes that value from the request parameters immediately and reflects it in the results (SHALL)

### Requirement: Status facet options do not self-filter
Status facet option loading MUST ignore the status facet's own filter values so multi-select behavior remains possible.

#### Scenario: Other status options remain visible after one is selected
- **WHEN** one status is selected and options are reloaded
- **THEN** other unselected status values remain visible (SHALL)

#### Scenario: Backend ignores mistaken status self-filter input
- **WHEN** the client accidentally includes `statuses` while loading status options
- **THEN** the server ignores that condition while computing status options (SHALL)

#### Scenario: Non-status filters still narrow the options
- **WHEN** filters such as time range, model, or account are applied
- **THEN** those filters still narrow the status options normally (SHALL)
