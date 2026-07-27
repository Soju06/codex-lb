## MODIFIED Requirements

### Requirement: Background usage refresh is staggered across accounts

Background usage refresh MUST distribute account refresh attempts across the configured usage refresh interval instead of refreshing every eligible account in one burst. Each scheduler slice MUST attempt at most one eligible account. Over a full cycle, all eligible accounts SHOULD be considered once.

Each slice MUST select its account before reading usage history and MUST scope its latest-usage lookups, updater input, warm-up candidate evaluation, and recoverable-status evaluation to that selected account. The scheduler MAY retain the full eligible account roster only to choose the deterministic rotation and calculate staggered warm-up phases; that roster MUST NOT cause usage-history reads, upstream refresh attempts, warm-up sends, or status mutations for an unrelated account in the slice. A selected-account refresh failure MUST NOT trigger same-slice fallback to another account. Database sessions used to load scheduler state MUST close before upstream network I/O begins, and concurrent follow-up work MUST NOT share an `AsyncSession`.

#### Scenario: Scheduler refreshes one account per slice

- **GIVEN** two active accounts are eligible for usage refresh
- **WHEN** the scheduler runs consecutive refresh slices
- **THEN** the first slice attempts one account
- **AND** the second slice attempts the other account
- **AND** cache invalidation for usage-derived routing state runs at the cycle boundary

#### Scenario: Unrefreshable accounts are skipped by scheduler rotation

- **GIVEN** one account is active
- **AND** one account is deactivated
- **AND** one account requires re-authentication
- **WHEN** the scheduler builds the refresh rotation
- **THEN** only the active account is considered

#### Scenario: Selected slot scopes usage history and follow-up work

- **GIVEN** two eligible accounts have stored primary, secondary, and monthly usage
- **AND** the first account is selected for the current scheduler slice
- **WHEN** the scheduler reads before/after usage and evaluates warm-up and recoverable status
- **THEN** every usage-history lookup is filtered to the first account
- **AND** only the first account is passed to usage refresh, warm-up candidate evaluation, and recoverable-status evaluation
- **AND** the second account cannot be mutated or contacted during that slice

#### Scenario: Warm-up phase cohort does not widen evaluation scope

- **GIVEN** multiple warm-up-enabled accounts participate in staggered-idle phase calculation
- **AND** one account is selected for the current usage-refresh slice
- **WHEN** refreshed usage is evaluated for warm-up
- **THEN** the phase calculation retains the eligible fleet cohort
- **AND** only the selected account can create a warm-up attempt or send warm-up traffic

#### Scenario: Selected-account failure does not fail over within the slice

- **GIVEN** two accounts are eligible for scheduler rotation
- **AND** the first account is selected
- **WHEN** that account's usage refresh fails
- **THEN** the scheduler does not attempt the second account in the same slice
- **AND** the second account remains eligible for its normal later slice

#### Scenario: Scheduler session closes before selected-account network work

- **GIVEN** the scheduler loaded the account roster and selected account usage
- **WHEN** the selected account's upstream refresh starts
- **THEN** the scheduler read session is already closed
- **AND** any concurrent warm-up follow-up owns an independent database session
