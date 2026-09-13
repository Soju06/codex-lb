## MODIFIED Requirements

### Requirement: Resilience toggles follow the dashboard value

Soft drain (the draining/probing health tiers), the deterministic failover decision and the circuit-breaker selection gate MUST be controlled by the `dashboard_settings` columns `soft_drain_enabled`, `deterministic_failover_enabled` and `circuit_breaker_enabled`. A NULL column MUST inherit the process environment value (the deprecated `CODEX_LB_*` alias) and then the code default, and a non-NULL column MUST win over both; the effective value MUST come from the single `configuration-tiers` resolver, and the settings API MUST report each toggle's effective value and provenance. Account selection MUST resolve the three toggles once from the dashboard-settings snapshot its caller obtained before entering runtime locks — the same snapshot that produced the concurrency caps — MUST apply that resolution to every reload of its selection inputs (sticky and non-sticky retries, exclusion- and security-filtered pools) and to opportunistic admission, and MUST NOT read the database, await the settings cache or read `get_settings().<toggle>` for them while holding a runtime lock or inside the retry loop. Force Probe settlement MUST take one snapshot before acquiring the account lock. The background state builds — the quota planner tick, the quota planner forecast endpoint and the usage-refresh recovery reconciliation — MUST resolve soft drain from one dashboard-settings snapshot taken per tick or per request outside any runtime lock (the settings cache, or the dashboard-settings row the usage-refresh cycle already read) and pass it into the state build, so the health tier they compute follows the dashboard toggle; they MUST NOT resolve the environment layer while a snapshot is available. Only a caller with no snapshot at all (tests, tools) resolves the environment layer, which is the pre-dashboard behaviour. Changing a toggle in the dashboard MUST take effect on the next selection on every replica without a restart.

#### Scenario: Dashboard turns soft drain off

- **GIVEN** `CODEX_LB_SOFT_DRAIN_ENABLED` is unset (default on) and an operator sets soft drain off in the dashboard
- **WHEN** the next selection evaluates an account whose primary usage is above the fixed drain threshold
- **THEN** the account stays in the healthy tier instead of entering the draining tier
- **AND** no database read or settings-cache await happened under the runtime lock

#### Scenario: Background state builds follow the dashboard soft-drain value

- **GIVEN** `CODEX_LB_SOFT_DRAIN_ENABLED` is unset (default on) and the dashboard stores `soft_drain_enabled = false`
- **WHEN** the quota planner tick or forecast endpoint builds its account states, or the usage-refresh recovery evaluates a recoverable account
- **THEN** the states are built with soft drain off, so an account above the fixed drain threshold stays in the healthy tier
- **AND** the snapshot was taken once for that tick or request, outside any runtime lock

#### Scenario: Dashboard turns deterministic failover off

- **GIVEN** an operator has set deterministic failover off in the dashboard
- **WHEN** a stream, compact or WebSocket attempt fails before the first event with a failover-eligible classification
- **THEN** the proxy surfaces the failure instead of retrying on the next account

#### Scenario: Dashboard enables the circuit-breaker gate the environment left off

- **GIVEN** `CODEX_LB_CIRCUIT_BREAKER_ENABLED=false` and an operator turns the circuit breaker on in the dashboard
- **AND** every account's breaker is open
- **WHEN** a selection runs
- **THEN** the balancer reports the upstream as degraded because the breakers are open, without a restart

#### Scenario: Inherited toggle follows a later environment change

- **GIVEN** a toggle's dashboard column is NULL
- **WHEN** the process environment value changes and the process restarts
- **THEN** the new environment value applies and the settings API reports `source: "env"` (or `"default"` when it equals the code default)

### Requirement: Routing weights and overload isolation are dashboard settings

The in-flight pressure penalty (`proxy_account_inflight_penalty_pct`), the leased-token weight (`proxy_account_lease_token_weight`), the account lease TTL (`proxy_account_lease_ttl_seconds`), the overload isolation window (`proxy_overload_isolation_seconds`) and the error-rate weighting switch (`proxy_account_error_rate_weighting_enabled`) MUST be `dashboard_settings` columns of the same name, resolved as code default < environment < dashboard: a NULL column inherits the process environment value (or the code default), and a non-NULL column wins over the environment. The first-boot seed and the migration MUST leave the columns NULL. The settings API MUST expose each effective value with a `provenance` entry and accept the tri-state update (omitted = unchanged, `null` = inherit, value = store) with the bounds of the corresponding `Settings` field; the in-flight penalty MUST additionally be bounded at 100 on write (an inherited environment value above 100 MUST still be readable), and a dashboard lease TTL MUST satisfy the same `account-lease-ttl-covers-*` timeout invariants that startup validation applies to the environment value, evaluated against the effective request budgets. The load balancer MUST NOT read the process settings for these values on the request path: the proxy service MUST resolve them once per selection or lease operation from the cached dashboard snapshot it already holds for that operation (the snapshot the concurrency caps are derived from) and pass them into account selection, opportunistic admission and lease acquisition, and the balancer MUST thread that snapshot through every runtime-lock section of the operation without reading settings. A path that carries no request snapshot (the stream error funnel recording an overload rejection, an unkeyed bridge session reacquiring its lease) MUST reuse the balancer's most recent request snapshot rather than read settings; the environment applies only before the first request has been served. The background state builds (the quota planner tick and forecast endpoint, the usage-refresh recovery reconciliation) MUST resolve the knobs from the dashboard-settings snapshot they take once per tick or per request and pass them into the state build; only a caller with no snapshot at all (tests, tools) resolves the environment alone. A changed value takes effect within the settings cache TTL without a restart, with these runtime semantics: a new isolation window applies to trips recorded after the change only — an account already isolated keeps its existing deadline, and storing `0` does not lift an active isolation; a new lease TTL applies at the next stale-lease reclaim pass to every existing lease (judged by its acquisition time). The environment variables remain as deprecated fallbacks for one release and are removed in the next minor.

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment sets `CODEX_LB_PROXY_ACCOUNT_INFLIGHT_PENALTY_PCT=2.5` and the dashboard stores `proxy_account_inflight_penalty_pct = 10`
- **WHEN** account states are built for a selection
- **THEN** each in-flight request adds 10 percentage points of pressure, not 2.5

#### Scenario: Background state builds resolve the knobs from the dashboard snapshot

- **GIVEN** the process environment leaves `proxy_account_inflight_penalty_pct` at 2.5 and the dashboard stores `proxy_account_inflight_penalty_pct = 37.5`
- **WHEN** the quota planner tick or forecast endpoint builds its account states, or the usage-refresh recovery evaluates a recoverable account
- **THEN** the state build receives the routing tunables resolved from the dashboard snapshot (in-flight penalty 37.5), not the environment value
- **AND** no settings read happens under a runtime lock

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
