## ADDED Requirements

### Requirement: Selection state expires elapsed usage windows

When building account selection state, the proxy SHALL treat any main-window usage sample (primary or secondary) whose `reset_at` timestamp has elapsed as a reset window: the derived used percentage becomes `0.0` and the derived reset timestamp is cleared, regardless of the sample's recorded used percentage. The rule SHALL apply after weekly-only primary remapping and SHALL mutate only derived selection inputs, not stored usage rows. Expired samples SHALL map to `0.0` rather than unknown so usage-derived status recovery still evaluates.

#### Scenario: Stale sub-100% primary sample stops gating selection

- **GIVEN** upstream stopped reporting a primary window for an account
- **AND** the account's last stored primary row reports 87% used with an elapsed `reset_at`
- **WHEN** selection state is built for that account
- **THEN** the derived primary usage is `0.0` with no reset timestamp
- **AND** the sample no longer holds the account in the soft-drain tier or above sticky budget-safety thresholds

#### Scenario: Expired sample still allows blocked-status recovery

- **GIVEN** an account persisted as `rate_limited` whose usage sample has an elapsed `reset_at`
- **WHEN** selection state is built for that account
- **THEN** the expired sample evaluates as `0.0` used rather than unknown
- **AND** usage-derived status recovery can still return the account to `active`

#### Scenario: Weekly-only remap happens before expiry

- **GIVEN** an account whose payload reports only a weekly window in the primary slot
- **WHEN** selection state is built
- **THEN** the weekly-primary remap into the secondary slot is evaluated on the raw samples
- **AND** the elapsed-reset expiry applies to the remapped derived values
