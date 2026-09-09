## MODIFIED Requirements

### Requirement: Dashboard-configurable account concurrency caps

The dashboard settings API MUST persist nonnegative per-account
`proxy_account_response_create_limit`, `proxy_account_stream_limit`, and
`proxy_account_stream_recovery_reserve` overrides, plus the
`proxy_api_key_fair_share_congestion_threshold_pct` override in the range
0-100. A settings row created for the first time MUST leave these four
overrides `NULL`; it MUST NOT copy the process environment values into the row.
Existing settings rows MUST use nullable stored overrides so a `NULL` value
continues to inherit the corresponding process environment value (or the code
default when the variable is unset) until an operator explicitly stores an
override, and an operator MAY clear an override to return to inheritance.
Precedence follows `configuration-tiers`: code default, then environment, then
a non-NULL dashboard value.

The settings response MUST expose each effective value, its environment
baseline value, and its nullable stored override. Updates MUST use tri-state semantics for these four override
fields: an absent field MUST leave the stored override unchanged, a field with
a numeric value MUST store that value as an override, and a field explicitly
set to `null` MUST clear the stored override so the effective value inherits
from the process environment.

#### Scenario: Explicit null clears a capacity override

- **GIVEN** a stored dashboard stream-cap override and an environment stream
  cap
- **WHEN** `PUT /api/settings` contains
  `proxyAccountStreamLimit: null`
- **THEN** the stored stream-cap override is `NULL`
- **AND** the response reports the environment stream cap as the effective
  value
- **AND** the response reports a `null` stream-cap override

#### Scenario: Operator changes caps without restart

- **GIVEN** the dashboard cache contains persisted account concurrency caps
- **WHEN** an operator updates one or more cap values through `PUT /api/settings`
- **THEN** the response returns the persisted effective values
- **AND** subsequent new selection and lease decisions use the updated cached
  values without mutating global process settings

#### Scenario: Omitted capacity field preserves its override

- **GIVEN** a stored dashboard capacity override
- **WHEN** an update omits that capacity field
- **THEN** the stored override remains unchanged
- **AND** the effective value remains unchanged

#### Scenario: Negative cap is rejected

- **WHEN** an operator supplies a negative account concurrency cap or recovery
  reserve
- **THEN** the settings API rejects the request
- **AND** the previously persisted values remain unchanged

#### Scenario: Explicit value remains a pinned override

- **GIVEN** an environment stream cap of 8 and no dashboard stream-cap
  override
- **WHEN** `PUT /api/settings` contains `proxyAccountStreamLimit: 8`
- **THEN** the stored stream-cap override is 8
- **AND** a later environment change does not alter the effective dashboard
  value until the override is cleared

#### Scenario: Operator edits caps in the dashboard

- **GIVEN** an operator opens routing settings
- **WHEN** the operator enters nonnegative integer cap values and saves them
- **THEN** the dashboard sends the edited values through the settings API
- **AND** `0` is presented as unlimited
- **AND** a bounded stream recovery reserve greater than the effective stream
  cap is rejected before saving

#### Scenario: Clearing one field does not modify sibling overrides

- **GIVEN** stored overrides for the response-create limit and stream limit
- **WHEN** only `proxyAccountStreamLimit` is explicitly cleared
- **THEN** the stream-limit override becomes `NULL`
- **AND** the response-create override remains unchanged

#### Scenario: Invalid capacity update is atomic

- **WHEN** an update contains an invalid capacity value or a recovery reserve
  greater than its effective stream limit
- **THEN** the settings API rejects the update
- **AND** all four stored capacity overrides remain unchanged

#### Scenario: Clear validation uses the environment baseline

- **GIVEN** a stored stream-limit override of 24, a stored recovery reserve of
  3, and an environment stream limit of 2
- **WHEN** only the stream-limit override is explicitly cleared
- **THEN** the settings API rejects the update before persistence
- **AND** the stored stream-limit override remains 24

#### Scenario: Environment change after first boot takes effect on a fresh install

- **GIVEN** a fresh install whose settings row was created while `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=8` was set and no operator has edited the stream cap
- **WHEN** the process is restarted with `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=12`
- **THEN** new stream selection and lease decisions use a cap of 12
- **AND** the settings API reports the stream cap as inherited from the environment
