# account-routing Delta

## ADDED Requirements

### Requirement: Upstream overload rejections back off and then isolate the account

The balancer SHALL keep a replica-local sliding window of upstream overload admission rejections (`server_is_overloaded`, `overloaded_error`) per account that successes do not reset. When the window trips, the account SHALL enter a bounded, exponentially growing **soft backoff** during which fresh unbound selection and fresh sticky bindings prefer other candidates. When the backoff level reaches the isolation trip level, the account SHALL instead be **isolated** for `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS` (default 1800; `0` disables isolation and keeps the soft backoff only). While isolated, established soft sticky owners MAY be released as specified by `sticky-session-operations`. In both stages the account MUST be dropped from a candidate pool only while at least one other candidate remains, and the configured strategy MUST judge eligibility of the remaining pool: when it rejects every overload-free candidate, selection MUST fall back to the full pool exactly as before. The backoff level MUST NOT decay while the account is backed off or isolated; it decays only after a quiet interval measured from the later of the last trip and the backoff deadline. Hard continuity owners MUST NOT be moved by either stage. The balancer MUST emit a warning when isolation engages, naming the account, level and isolation interval.

#### Scenario: Sustained rejection escalates from soft backoff to isolation

- **GIVEN** an account whose overload window has tripped twice (soft backoff)
- **WHEN** it trips a third time
- **THEN** the account is isolated for the configured isolation interval instead of the next soft interval
- **AND** a warning `Account overload isolation engaged` is logged with the level and interval

#### Scenario: Isolation is disabled by a zero interval

- **GIVEN** `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS=0`
- **WHEN** an account's overload window trips at or beyond the isolation level
- **THEN** it receives the capped soft backoff interval and is never marked isolated

#### Scenario: Leaving isolation while still rejected re-isolates

- **GIVEN** an account whose isolation deadline just passed
- **WHEN** its overload window trips again within the decay interval
- **THEN** its level has not decayed and the account is isolated again

#### Scenario: A lone candidate is never held out

- **GIVEN** the only selectable account is isolated
- **WHEN** a request selects an account
- **THEN** the isolated account is selected rather than failing with `No available accounts`

### Requirement: Weighted strategies discount recent upstream error rate

The balancer SHALL keep a replica-local window (600 s) of upstream outcomes per account: successes recorded by `record_success` and the account-attributable transient failures recorded by `record_errors`. Rate-limit, quota, permanent and account-neutral failures MUST NOT be counted. When `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED` is true (default) and the window holds at least 10 outcomes, the `capacity_weighted` and `relative_availability` strategies MUST multiply the candidate's draw weight by `max(0.05, 1 - error_rate)`; with fewer outcomes or the setting disabled the multiplier MUST be neutral. The multiplier MUST NOT change `relative_availability` top-k membership or any deterministic probe pick, and deterministic strategies (`round_robin`, `usage_weighted`, `fill_first`, `sequential_drain`, `reset_drain`, `single_account`) MUST be unaffected. The discount MUST lift as the window clears without requiring a success.

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

- **GIVEN** `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED=false`
- **WHEN** an account has failed every request in the window
- **THEN** its draw weight multiplier is `1.0`
