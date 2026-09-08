## MODIFIED Requirements

### Requirement: Dashboard-configurable account concurrency caps

The dashboard settings API MUST persist nonnegative per-account `proxy_account_response_create_limit`, `proxy_account_stream_limit`, and `proxy_account_stream_recovery_reserve` overrides. A settings row created for the first time MUST leave these overrides NULL; it MUST NOT copy the process environment values into the row. A NULL override inherits the corresponding process environment value (or the code default when the variable is unset) until explicitly changed by an operator, and an operator MAY clear an override to return to inheritance. Precedence follows `configuration-tiers`: code default, then environment, then a non-NULL dashboard value.

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

#### Scenario: Environment change after first boot takes effect on a fresh install

- **GIVEN** a fresh install whose settings row was created while `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=8` was set and no operator has edited the stream cap
- **WHEN** the process is restarted with `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=12`
- **THEN** new stream selection and lease decisions use a cap of 12
- **AND** the settings API reports the stream cap as inherited from the environment
