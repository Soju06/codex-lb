## ADDED Requirements

### Requirement: Phase planning is scoped to short rolling windows

An account SHALL be phase-plannable for scheduler warmup candidacy and cold-start routing costs only when its selection state carries a primary window sample with duration metadata of at most 24 hours (weekly and monthly windows are not phase-plannable; samples without duration metadata are not phase-plannable). The warm-up execution gate and warmup-effect observed confidence SHALL treat positive evidence of a long window in the primary slot as disqualifying, while absent samples or samples without duration metadata keep legacy bootstrap behavior. Active/cold window state SHALL derive from the primary window sample's reset timestamp, not from blocked-status reset markers, and only phase-plannable accounts SHALL count as having active phase windows — a long unremapped primary sample MUST NOT feed expiring-window bonuses, active-reset stagger anchors, or simulated pool capacity.

#### Scenario: Weekly-only account is not planned

- **GIVEN** an account whose usage reports only a weekly window
- **WHEN** the planner evaluates warmup candidates and routing costs
- **THEN** the account receives no warmup actions and no cold-start costs

#### Scenario: Mixed pool plans only short-window accounts

- **GIVEN** one account with a 300-minute primary window sample and one account with only weekly usage
- **WHEN** the planner plans warmups during the prewarm band
- **THEN** only the short-window account is considered

#### Scenario: Healthy active window is not treated as cold

- **GIVEN** a healthy `active` account whose primary window sample has an unexpired reset timestamp
- **WHEN** the planner builds routing costs
- **THEN** the account is treated as having an active window
- **AND** it receives no cold-start cost

#### Scenario: Execution gate refuses accounts with a long-window primary sample

- **WHEN** a warm-now request targets an account whose latest primary-slot sample reports a weekly or monthly window duration
- **THEN** the execution gate refuses with a stable `no_short_window` reason

#### Scenario: Execution gate refuses superseded short-window samples

- **GIVEN** an account whose latest primary-slot sample reports a short window
- **AND** a strictly newer long-window row proves a later refresh no longer reported the short window
- **WHEN** a warm-now request targets that account
- **THEN** the execution gate refuses with `no_short_window`

#### Scenario: Superseded short-window samples lose plannability

- **GIVEN** an account whose primary sample is strictly older than its long-window row
- **WHEN** selection state is built
- **THEN** the derived short-window duration is cleared whether or not the stale sample's reset has elapsed
- **AND** the planner does not treat the account as having a short phase window

### Requirement: Phase math derives from observed window durations

Candidate warmup start times, planned window resets, pool-simulation window spans, and synchronization penalties SHALL use each account's observed primary window duration. Planner actions SHALL carry the window duration they were planned with, and cross-account reset math SHALL use each action's own duration.

#### Scenario: Peak alignment uses the observed duration

- **GIVEN** an account whose primary window sample reports a 60-minute duration
- **WHEN** the planner derives candidate start times for a forecast peak
- **THEN** peak-aligned candidates subtract 60 minutes rather than 5 hours
- **AND** the planned action records a 60-minute window duration
