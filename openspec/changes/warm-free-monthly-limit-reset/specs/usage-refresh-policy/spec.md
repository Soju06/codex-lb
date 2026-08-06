## ADDED Requirements

### Requirement: Reset-confirmed warm-up follows the plan-applicable long window

When reset-confirmed limit warm-up evaluates an account's selected long quota
window, the system MUST use the monthly usage row when that account's plan has
monthly quota capacity and MUST otherwise use the secondary usage row. The
persisted warm-up attempt MUST retain the canonical window name from the
selected usage row.

#### Scenario: Free monthly reset triggers one monthly warm-up

- **GIVEN** limit warm-up is enabled globally and for a free-plan account
- **AND** long-window warm-up is selected
- **AND** the account's previous monthly usage sample was exhausted
- **WHEN** background usage refresh records a newer monthly sample with
  available quota and a later `reset_at`
- **THEN** the system sends at most one warm-up request for that
  account/monthly/reset tuple
- **AND** the durable warm-up attempt records `window="monthly"`

#### Scenario: Paid plans retain secondary long-window warm-up

- **GIVEN** an account plan has no monthly quota capacity
- **AND** primary and secondary usage samples are available
- **WHEN** background usage refresh evaluates long-window warm-up
- **THEN** the system uses the secondary usage row
- **AND** it does not substitute an unrelated monthly row

#### Scenario: First monthly sample is not treated as a reset

- **GIVEN** a free-plan account has no previous monthly usage sample
- **AND** its latest secondary sample is exhausted
- **WHEN** background usage refresh records the account's first monthly sample
- **THEN** the system does not compare the secondary and monthly `reset_at`
  values as one window
- **AND** it does not send a reset-confirmed warm-up for that transition

#### Scenario: Scheduler scopes monthly snapshots to the selected account

- **GIVEN** multiple accounts are eligible for background usage refresh
- **AND** one account is selected for the current scheduler slice
- **WHEN** the scheduler loads before and after usage for warm-up evaluation
- **THEN** monthly lookups are filtered to the selected account
- **AND** monthly usage from another account cannot create a warm-up attempt
