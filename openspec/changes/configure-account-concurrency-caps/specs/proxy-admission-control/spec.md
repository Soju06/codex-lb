## ADDED Requirements

### Requirement: Dashboard-configurable account concurrency caps

The dashboard settings API MUST persist nonnegative per-account `proxy_account_response_create_limit`, `proxy_account_stream_limit`, and `proxy_account_stream_recovery_reserve` overrides. A settings row created for the first time MUST persist the process environment values for those settings. Existing settings rows upgraded to this capability MUST use nullable overrides so a NULL value continues to inherit the corresponding process environment value until explicitly changed by an operator.

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

### Requirement: Cached caps govern runtime admission

New account selection, account lease acquisition, opportunistic admission, and account-cap error reporting MUST use one dashboard-settings cache snapshot obtained before entering runtime locks. These paths MUST NOT read the database or await the dashboard settings cache while holding a runtime lock.

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment stream cap differs from the persisted dashboard stream cap
- **WHEN** a new stream selection or lease acquisition occurs
- **THEN** the persisted cached dashboard cap controls the decision

### Requirement: Stream recovery reserve remains a selection reserve

The configured stream recovery reserve MUST remain a subtractive reserve for ordinary stream selection. Recovery selection without an ordinary reserve MAY use the full stream cap. A nonpositive stream cap continues to mean unlimited streams.

#### Scenario: Recovery may use a reserved slot

- **GIVEN** ordinary stream selection has consumed the configured ordinary capacity
- **WHEN** recovery stream selection is attempted without an ordinary reserve
- **THEN** it may acquire a remaining slot up to the configured stream cap
