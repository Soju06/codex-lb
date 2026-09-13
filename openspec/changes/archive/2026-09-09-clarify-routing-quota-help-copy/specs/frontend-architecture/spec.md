MUST make routing, sticky affinity, quota thresholds, warm-up, and account
eligibility understandable from dashboard copy alone.

## ADDED Requirements

### Requirement: Sticky-threads copy distinguishes soft routing from hard continuation affinity

The routing settings SHALL describe `Sticky threads` as a soft preference and
SHALL state that disabling it does not disable hard Codex continuation
affinity for requests that carry continuation state.

#### Scenario: Sticky threads help copy

- **WHEN** the routing settings section renders
- **THEN** the sticky-threads description identifies the toggle as a soft preference
- **AND** an adjacent note states that hard Codex continuation affinity is not disabled by the toggle

### Requirement: Sticky thresholds are presented in percent used with a remaining equivalent

The sticky reallocation threshold controls SHALL name the quota window they
apply to, SHALL state that the value is percent used, and SHALL show the
quota-arithmetic remaining percent for a valid threshold value, qualified so
it is not presented as the exact account-page remaining figure (routing adds
temporary in-flight pressure on top of reported usage).

#### Scenario: Threshold unit hint

- **GIVEN** the sticky secondary threshold input holds the valid value `70`
- **WHEN** the routing settings section renders
- **THEN** a hint shows `70% used` alongside `30% remaining` in quota terms
- **AND** the quota-window explainer states that in-flight work counts as temporary extra usage

#### Scenario: Quota window explainer

- **WHEN** the routing settings section renders
- **THEN** an explainer identifies primary quota as the 5-hour window and secondary quota as the longer weekly window (monthly on plans without a weekly window)
- **AND** it states that account pages show percent remaining while the thresholds are percent used

### Requirement: Prefer-earlier-reset and limit warm-up copy describe actual behavior

The routing settings SHALL describe `Prefer earlier reset` as preferring
otherwise-eligible accounts whose selected quota window resets sooner, and
SHALL describe limit warm-up as sending one small probe request that consumes
a small amount of quota when an opted-in account's quota window is confirmed
to have newly reset.

#### Scenario: Prefer earlier reset help copy

- **WHEN** the routing settings section renders
- **THEN** the prefer-earlier-reset description says selection prefers accounts whose selected quota window resets sooner
- **AND** it names the strategies the preference applies to (capacity weighted, usage weighted, and fill first)

#### Scenario: Limit warm-up help copy

- **WHEN** the routing settings section renders
- **THEN** the limit warm-up description says a probe is sent when an opted-in account's quota window is confirmed to have newly reset
- **AND** it states that probes are real requests and consume a small amount of quota

### Requirement: Active status is presented as displayed status, not per-request eligibility

The accounts list SHALL present a visible note that displayed status does not
guarantee per-request eligibility, and SHALL annotate active rows and their
status badges with the same hint for pointer and assistive-technology users.

#### Scenario: Accounts list eligibility note

- **GIVEN** the accounts list contains at least one account
- **WHEN** the list renders
- **THEN** a visible note states that individual requests can still skip an `Active` account

#### Scenario: Active badge eligibility hint

- **GIVEN** an account whose status is `active`
- **WHEN** its accounts-list entry renders
- **THEN** the focusable account row and the status badge carry the hint as a native tooltip and accessible description
- **AND** non-active statuses do not carry that hint
