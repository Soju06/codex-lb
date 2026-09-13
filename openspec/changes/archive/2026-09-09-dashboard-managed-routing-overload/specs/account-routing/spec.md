## ADDED Requirements

### Requirement: Routing weights and overload isolation are dashboard settings

The in-flight pressure penalty (`proxy_account_inflight_penalty_pct`), the leased-token weight (`proxy_account_lease_token_weight`), the account lease TTL (`proxy_account_lease_ttl_seconds`), the overload isolation window (`proxy_overload_isolation_seconds`) and the error-rate weighting switch (`proxy_account_error_rate_weighting_enabled`) MUST be `dashboard_settings` columns of the same name, resolved as code default < environment < dashboard: a NULL column inherits the process environment value (or the code default), and a non-NULL column wins over the environment. The first-boot seed and the migration MUST leave the columns NULL. The settings API MUST expose each effective value with a `provenance` entry and accept the tri-state update (omitted = unchanged, `null` = inherit, value = store) with the bounds of the corresponding `Settings` field; the in-flight penalty MUST additionally be bounded at 100 on write (an inherited environment value above 100 MUST still be readable), and a dashboard lease TTL MUST satisfy the same `account-lease-ttl-covers-*` timeout invariants that startup validation applies to the environment value, evaluated against the effective request budgets. The load balancer MUST NOT read the process settings for these values on the request path: the proxy service MUST resolve them once per selection or lease operation from the cached dashboard snapshot it already holds for that operation (the snapshot the concurrency caps are derived from) and pass them into account selection, opportunistic admission and lease acquisition, and the balancer MUST thread that snapshot through every runtime-lock section of the operation without reading settings. A path that carries no request snapshot (the stream error funnel recording an overload rejection, an unkeyed bridge session reacquiring its lease) MUST reuse the balancer's most recent request snapshot rather than read settings; the environment applies only before the first request has been served. A changed value takes effect within the settings cache TTL without a restart, with these runtime semantics: a new isolation window applies to trips recorded after the change only — an account already isolated keeps its existing deadline, and storing `0` does not lift an active isolation; a new lease TTL applies at the next stale-lease reclaim pass to every existing lease (judged by its acquisition time). The environment variables remain as deprecated fallbacks for one release and are removed in the next minor.

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment sets `CODEX_LB_PROXY_ACCOUNT_INFLIGHT_PENALTY_PCT=2.5` and the dashboard stores `proxy_account_inflight_penalty_pct = 10`
- **WHEN** account states are built for a selection
- **THEN** each in-flight request adds 10 percentage points of pressure, not 2.5

#### Scenario: Cleared dashboard value returns to the environment

- **GIVEN** the dashboard stores `proxy_account_lease_ttl_seconds = 1200` while the environment sets `CODEX_LB_PROXY_ACCOUNT_LEASE_TTL_SECONDS=1800`
- **WHEN** `PUT /api/settings` sends `proxyAccountLeaseTtlSeconds: null`
- **THEN** the response reports the effective TTL 1800 with `provenance.proxy_account_lease_ttl_seconds.source` `"env"`
- **AND** the next request snapshot judges stale leases against 1800 seconds

#### Scenario: Isolation window change applies to future trips only

- **GIVEN** an account isolated for 1800 seconds with 1000 seconds remaining
- **WHEN** the dashboard stores `proxy_overload_isolation_seconds = 0` (or 240)
- **THEN** the account stays isolated until its existing deadline
- **AND** the next account whose window trips at the isolation level receives the soft backoff only (or 240 seconds)

#### Scenario: Lease TTL change applies at the next reclaim pass

- **GIVEN** a response-create lease acquired 700 seconds ago while the effective lease TTL was 900
- **WHEN** the dashboard stores `proxy_account_lease_ttl_seconds = 600` and the next selection or lease acquisition runs its stale-lease reclaim
- **THEN** that lease is reclaimed as stale in that pass

#### Scenario: Selection never reads settings under the runtime lock

- **GIVEN** a request whose selection acquires the balancer's runtime lock
- **WHEN** the balancer builds states, reclaims stale leases and evaluates draw weights
- **THEN** every knob comes from the `RoutingTunables` snapshot passed in for that operation and no settings or database read happens inside the lock section

#### Scenario: Out-of-bounds value is rejected

- **WHEN** `PUT /api/settings` sends `proxyAccountInflightPenaltyPct: 150`, `proxyAccountLeaseTtlSeconds: 0`, or a lease TTL below the proxy or compact request budget (for example 120 with the default 600 s budget)
- **THEN** the request is rejected with a validation error naming the violated invariant and the stored values are unchanged

## MODIFIED Requirements

### Requirement: Upstream overload rejections back off and then isolate the account

The balancer SHALL keep a replica-local sliding window of upstream overload admission rejections (`server_is_overloaded`, `overloaded_error`) per account that successes do not reset. When the window trips, the account SHALL enter a bounded, exponentially growing **soft backoff** during which fresh unbound selection and fresh sticky bindings prefer other candidates. When the backoff level reaches the isolation trip level, the account SHALL instead be **isolated** for the dashboard setting `proxy_overload_isolation_seconds` (default 1800; `0` disables isolation and keeps the soft backoff only; the environment variable `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS` is the deprecated fallback the dashboard inherits while its value is unset). The window used for an isolation MUST be the one carried by the balancer's most recent request snapshot, never a settings read from the error funnel. While isolated, established soft sticky owners MAY be released as specified by `sticky-session-operations`. In both stages the account MUST be dropped from a candidate pool only while at least one other candidate remains, and the configured strategy MUST judge eligibility of the remaining pool: when it rejects every overload-free candidate, selection MUST fall back to the full pool exactly as before. The backoff level MUST NOT decay while the account is backed off or isolated; it decays only after a quiet interval measured from the later of the last trip and the backoff deadline. Hard continuity owners MUST NOT be moved by either stage. The balancer MUST emit a warning when isolation engages, naming the account, level and isolation interval.

#### Scenario: Sustained rejection escalates from soft backoff to isolation

- **GIVEN** an account whose overload window has tripped twice (soft backoff)
- **WHEN** it trips a third time
- **THEN** the account is isolated for the configured isolation interval instead of the next soft interval
- **AND** a warning `Account overload isolation engaged` is logged with the level and interval

#### Scenario: Isolation is disabled by a zero interval

- **GIVEN** the dashboard stores `proxy_overload_isolation_seconds = 0` (or the column is NULL and `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS=0`)
- **WHEN** an account's overload window trips at or beyond the isolation level
- **THEN** it receives the capped soft backoff interval and is never marked isolated

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment leaves the isolation window at 1800 seconds and `PUT /api/settings` stores `proxyOverloadIsolationSeconds: 240`
- **WHEN** a request has been served after the change and an account's overload window then trips at the isolation level
- **THEN** the account is isolated for 240 seconds without a restart

#### Scenario: Leaving isolation while still rejected re-isolates

- **GIVEN** an account whose isolation deadline just passed
- **WHEN** its overload window trips again within the decay interval
- **THEN** its level has not decayed and the account is isolated again

#### Scenario: A lone candidate is never held out

- **GIVEN** the only selectable account is isolated
- **WHEN** a request selects an account
- **THEN** the isolated account is selected rather than failing with `No available accounts`

### Requirement: Weighted strategies discount recent upstream error rate

The balancer SHALL keep a replica-local window (600 s) of upstream outcomes per account: successes recorded by `record_success` and the account-attributable transient failures recorded by `record_errors`. Rate-limit, quota, permanent and account-neutral failures MUST NOT be counted. When the dashboard setting `proxy_account_error_rate_weighting_enabled` is true (default; the environment variable `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED` is the deprecated fallback the dashboard inherits while its value is unset) and the window holds at least 10 outcomes, the `capacity_weighted` and `relative_availability` strategies MUST multiply the candidate's draw weight by `max(0.05, 1 - error_rate)`; with fewer outcomes or the setting disabled the multiplier MUST be neutral. The switch MUST be read from the request's `RoutingTunables` snapshot when states are built, never from the process settings inside selection. The multiplier MUST NOT change `relative_availability` top-k membership or any deterministic probe pick, and deterministic strategies (`round_robin`, `usage_weighted`, `fill_first`, `sequential_drain`, `reset_drain`, `single_account`) MUST be unaffected. The discount MUST lift as the window clears without requiring a success.

#### Scenario: A flaky account receives proportionally less weighted traffic

- **GIVEN** two accounts with equal remaining credits under `capacity_weighted`
- **AND** one of them recorded 12 transient failures interleaved with 12 successes in the last ten minutes (so its `error_count` latch is zero)
- **WHEN** fresh selections are drawn
- **THEN** the flaky account is drawn about half as often as the clean one
- **AND** it is still drawn (the weight floor keeps sampling it)

#### Scenario: Thin evidence is neutral

- **GIVEN** an account with nine failures and no successes in the window
- **WHEN** its draw weight is computed
- **THEN** the multiplier is `1.0`

#### Scenario: Weighting can be disabled

- **GIVEN** the dashboard stores `proxy_account_error_rate_weighting_enabled = false` (or the column is NULL and `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED=false`)
- **WHEN** an account has failed every request in the window
- **THEN** its draw weight multiplier is `1.0`

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment sets `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED=true` and the dashboard stores `false`
- **WHEN** states are built for a weighted selection
- **THEN** every candidate's multiplier is `1.0`
