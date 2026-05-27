## ADDED Requirements

### Requirement: Dashboard weekly credits pace

The dashboard SHALL show weekly quota pace when account weekly capacity credits, remaining credits, reset time, and window length are available. The pace calculation MUST use credit totals rather than averaging per-account percentages, because weekly ChatGPT quota credits are not the same unit as raw request tokens.

#### Scenario: Weekly credits pace uses account reset deadlines

- **WHEN** multiple accounts have weekly quota data with different `resetAtSecondary` values
- **THEN** the frontend computes each account's expected remaining weekly credits from that account's own reset time and window length before summing totals

#### Scenario: Over-plan pace shows pause needed to break even

- **WHEN** actual remaining weekly credits are lower than scheduled remaining weekly credits
- **THEN** the dashboard shows recovery options including how long weekly usage should pause for scheduled remaining credits to catch up
- **AND** the dashboard shows a throttle option for reducing parallel weekly-credit load
- **AND** the dashboard shows how many Pro-sized weekly credit pools would cover the current over-plan credits
- **AND** the Pro-sized pool recommendation shows the fractional pool equivalent before any rounded whole-account count
- **AND** the pause calculation accounts for each account's own reset deadline rather than using one global weekly burn rate

#### Scenario: Near-reset depletion is not a false alarm

- **WHEN** an account has consumed 99% of its weekly quota and 99% of its weekly window has elapsed
- **THEN** the weekly pace treats that account as on pace rather than over plan

#### Scenario: Missing weekly credit data is omitted

- **WHEN** an account is missing weekly capacity credits, remaining credits, reset time, or window length
- **THEN** that account is omitted from weekly pace calculation

#### Scenario: No valid weekly credit data hides pace

- **WHEN** no account has complete weekly credits pace data
- **THEN** the dashboard does not render a fake weekly pace value

### Requirement: Account weekly trend planned line

The account detail usage trend SHALL include an ideal weekly remaining line when weekly reset timing is available, so operators can compare actual weekly remaining credits against the linear schedule between weekly resets.

#### Scenario: Weekly trend shows planned depletion between resets

- **WHEN** account trend buckets include weekly reset time and window length
- **THEN** the account 7-day trend includes a dashed weekly plan line computed from each bucket's reset deadline and window length

#### Scenario: Weekly trend plan restarts after reset

- **WHEN** weekly trend buckets cross into a new reset window with a new reset deadline
- **THEN** the planned line jumps back toward full remaining capacity for the new weekly window instead of continuing one global diagonal
