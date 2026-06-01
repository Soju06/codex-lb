# proxy-admission-control Spec Delta — Add `fill_first` routing strategy

## ADDED Requirements

### Requirement: The fill_first routing strategy MUST select the highest-usage eligible account deterministically

The load balancer MUST pick a single account from the effective candidate
pool by selecting the highest primary 5h `used_percent` when the configured
`routing_strategy` is `fill_first`, treating an unknown `used_percent` as
`0.0`.

When two or more candidates share the same primary `used_percent`, the
balancer MUST prefer the candidate with the **higher** secondary
(weekly) `used_percent` — i.e. the one with the least remaining weekly
capacity — so the most-saturated account is drained first and the
freshest account is preserved for later cycles. An unknown
`secondary_used_percent` MUST be treated as `0.0` for this comparison.
`account_id` ascending MUST be the final stable tiebreaker.

The strategy MUST NOT use randomness. For a fixed snapshot of account
states and clock value, repeated invocations MUST return the same
account.

The strategy MUST reuse the existing effective candidate pool (preferring
healthy accounts, then probing, then draining, falling back to all
available accounts only when no higher-tier candidate exists). It MUST
NOT bypass error backoff, rate-limit cooldown, quota-exceeded cooldown,
or any other availability gate enforced by `select_account`.

When `prefer_earlier_reset` is enabled, `fill_first` MUST narrow the
candidate pool to accounts whose secondary reset bucket is earliest
before applying the highest-`used_percent` ranking, mirroring the
`capacity_weighted` strategy.

#### Scenario: Highest primary usage wins

- **GIVEN** the routing strategy is `fill_first`
- **AND** all eligible accounts share `health_tier = HEALTHY`
- **AND** account `A` has primary `used_percent = 30.0`,
  account `B` has primary `used_percent = 5.0`,
  and account `C` has primary `used_percent = 0.0`
- **WHEN** an account is selected
- **THEN** account `A` is returned

#### Scenario: Stable selection across consecutive calls

- **GIVEN** the routing strategy is `fill_first`
- **AND** the eligible pool and clock are unchanged between calls
- **WHEN** the balancer is invoked repeatedly
- **THEN** the same account is returned every time

#### Scenario: Selection moves on when the current pick leaves the pool

- **GIVEN** the routing strategy is `fill_first`
- **AND** the previously selected account becomes `RATE_LIMITED`,
  `QUOTA_EXCEEDED`, enters cooldown, or transitions to `DRAINING`
  while at least one other healthy account remains
- **WHEN** the balancer is invoked
- **THEN** the next-highest-`used_percent` healthy account is returned
- **AND** no random draw influences the outcome

#### Scenario: Highest secondary usage breaks primary ties

- **GIVEN** the routing strategy is `fill_first`
- **AND** three eligible accounts share primary `used_percent = 99.0`
- **AND** account `alpha` has secondary `used_percent = 29.0`,
  account `bravo` has secondary `used_percent = 98.0`,
  and account `charlie` has secondary `used_percent = 93.0`
- **WHEN** an account is selected
- **THEN** account `bravo` is returned

#### Scenario: Tiebreak by account id when both windows tie

- **GIVEN** the routing strategy is `fill_first`
- **AND** two eligible accounts share the same primary `used_percent`
- **AND** they also share the same secondary `used_percent`
- **WHEN** the balancer is invoked
- **THEN** the account with the lexicographically smaller `account_id`
  is returned
