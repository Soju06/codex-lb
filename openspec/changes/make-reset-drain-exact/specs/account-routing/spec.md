## MODIFIED Requirements

### Requirement: Reset drain routing

The proxy account selector SHALL support a `reset_drain` routing strategy. The
strategy SHALL evaluate only accounts that pass the existing eligibility,
model-plan, quota, cooldown, circuit-breaker, and budget-safety gates, then
select the usable account with the earliest exact future secondary quota reset
timestamp. When secondary reset data is unavailable, it SHALL fall back to the
exact primary reset timestamp. Accounts without either reset timestamp SHALL
sort after accounts with a known future reset. Only accounts with the same
effective reset timestamp SHALL be compared by remaining usable quota, with
more remaining quota preferred before the stable account tie-breaker.

#### Scenario: Exact soonest weekly reset is selected

- **GIVEN** multiple healthy eligible accounts with usable quota
- **AND** their secondary quota windows reset at different times, including within the same 24-hour period
- **WHEN** account selection uses `reset_drain`
- **THEN** the usable account with the earliest exact secondary reset timestamp is selected

#### Scenario: Same-reset accounts drain higher remaining quota first

- **GIVEN** multiple healthy eligible accounts with the same effective reset timestamp
- **WHEN** account selection uses `reset_drain`
- **THEN** the account with more remaining usable quota is selected

#### Scenario: Unknown weekly reset falls back safely

- **GIVEN** one eligible account has no secondary reset but has a future primary reset
- **AND** another eligible account has neither reset timestamp
- **WHEN** account selection uses `reset_drain`
- **THEN** the account with the known primary reset is selected
