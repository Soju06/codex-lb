## ADDED Requirements

### Requirement: Dashboard account cards show live credit state

Account summary responses SHALL expose the latest upstream credit metadata for
each account as nullable `creditsHas`, `creditsUnlimited`, and `creditsBalance`
fields. The dashboard account schema SHALL accept those fields.

The dashboard account card SHALL render a compact Credits row. If
`creditsUnlimited` is true, the value SHALL be `Unlimited`. Otherwise, when a
numeric credit balance is available it SHALL render that balance. If no credit
balance is available, the card MAY fall back to the account's remaining weekly
or primary credit value, and SHALL render `-` when no credit value is known.

#### Scenario: Unlimited credits render explicitly

- **WHEN** an account summary has `creditsUnlimited = true`
- **THEN** the dashboard account card shows `Credits: Unlimited`

#### Scenario: Positive credit balance renders on the card

- **WHEN** an account summary includes `creditsBalance = 1.5`
- **THEN** the dashboard account card shows that numeric credit balance

#### Scenario: Missing credit data renders a placeholder

- **WHEN** an account summary has no credit balance and no remaining credit fallback
- **THEN** the dashboard account card shows `Credits: -`
