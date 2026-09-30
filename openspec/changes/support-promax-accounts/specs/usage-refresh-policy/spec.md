## ADDED Requirements

### Requirement: Pro Max percentage-only subscription usage

The system SHALL recognize and preserve the `promax` account plan. It SHALL
retain reported quota percentages, durations and reset times without
assigning an unverified absolute subscription allowance. Purchased credits
SHALL remain separate from included subscription capacity.

#### Scenario: Weekly-only Pro Max quota
- **WHEN** Pro Max reports a 604800-second primary-slot window at 20% used
  and no secondary slot
- **THEN** its normalized weekly remaining percentage is 80%
- **AND** its short-window usage remains absent
- **AND** its absolute included capacity and remaining credits remain unknown

### Requirement: Explicit unquantified usage coverage

Usage-window summaries SHALL report `unquantifiedAccountCount` for reported
Pro Max windows without a known allowance. Existing numeric credit fields
SHALL describe only the known-capacity subtotal. Per-account history and
window items SHALL expose `capacityKnown` so unknown credits are not
presented as zero allowance.

#### Scenario: Mixed weekly subtotal
- **WHEN** Pro at 40%, Plus at 50% and Pro Max at 20% have reported weekly usage
- **THEN** weekly estimated capacity is 57960 and remaining credits are 34020
- **AND** the weekly unquantified-account count is 1
- **AND** an absent Pro Max short window does not increment the short count
