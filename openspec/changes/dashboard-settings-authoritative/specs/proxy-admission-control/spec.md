# proxy-admission-control

## MODIFIED Requirements

### Requirement: Dashboard-configurable account concurrency caps

The dashboard settings API MUST persist nonnegative per-account `proxy_account_response_create_limit`, `proxy_account_stream_limit`, and `proxy_account_stream_recovery_reserve` overrides. These overrides, together with `proxy_api_key_fair_share_congestion_threshold_pct`, MUST be nullable: a `NULL` stored value inherits the corresponding process environment value at read time until an operator explicitly stores an override. A settings row created for the first time MUST seed these four overrides as `NULL` and MUST NOT copy the process environment values into the row. Existing settings rows keep their stored values; a stored non-NULL value is a dashboard override and MUST be reported as such.

#### Scenario: Fresh settings row inherits the environment

- **GIVEN** no settings row exists and the process environment stream cap is 13
- **WHEN** the settings row is created
- **THEN** the four stored capacity overrides are `NULL`
- **AND** `GET /api/settings` reports the stream cap effective value 13, environment value 13, and a `null` override

#### Scenario: Environment change is honoured while no override is stored

- **GIVEN** a settings row whose stream-cap override is `NULL`
- **WHEN** the process is restarted with a different `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT`
- **THEN** the effective stream cap follows the new environment value without any change to the stored row

#### Scenario: Operator changes caps without restart

- **GIVEN** the dashboard cache contains persisted account concurrency caps
- **WHEN** an operator updates one or more cap values through `PUT /api/settings`
- **THEN** the response returns the persisted values
- **AND** subsequent new selection and lease decisions use the updated cached values without mutating global process settings

#### Scenario: Negative cap is rejected

- **WHEN** an operator supplies a negative account concurrency cap or recovery reserve
- **THEN** the settings API rejects the request
- **AND** the previously persisted values remain unchanged

#### Scenario: Operator edits caps in the dashboard

- **GIVEN** an operator opens routing settings
- **WHEN** the operator enters nonnegative integer cap values and saves them
- **THEN** the dashboard sends all three values through the settings API
- **AND** `0` is presented as unlimited
- **AND** a bounded stream recovery reserve greater than the stream cap is rejected before saving
