## MODIFIED Requirements

### Requirement: Explicit unquantified usage coverage

Usage-window summaries SHALL report `unquantifiedAccountCount` for reported
Pro Max windows without a known allowance. Existing numeric credit fields
SHALL describe only the known-capacity subtotal. Per-account history and
window items SHALL expose `capacityKnown` so unknown credits are not
presented as zero allowance.

Dashboard overview and projections SHALL omit fleet weekly credit pace when
an eligible Pro Max account has a fresh secondary usage observation, including
when its optional window duration is missing or zero.

#### Scenario: Mixed weekly subtotal
- **WHEN** Pro at 40%, Plus at 50% and Pro Max at 20% have reported weekly usage
- **THEN** weekly estimated capacity is 57960 and remaining credits are 34020
- **AND** the weekly unquantified-account count is 1
- **AND** an absent Pro Max short window does not increment the short count

#### Scenario: Recorded usage omits optional reset metadata
- **WHEN** a persisted Pro Max percentage observation omits its reset timestamp
  or window duration
- **THEN** its absolute allowance remains unquantified
- **AND** its per-account capacity is not reported as known
- **AND** synthetic rows for absent usage do not count as observations

#### Scenario: Fresh secondary usage has no usable duration
- **WHEN** an eligible Pro Max account has a fresh secondary percentage sample
  with a missing or zero duration alongside a known-capacity Pro weekly window
- **THEN** dashboard overview and projections return no fleet weekly credit pace
- **AND** the missing duration does not make the Max observation disappear
