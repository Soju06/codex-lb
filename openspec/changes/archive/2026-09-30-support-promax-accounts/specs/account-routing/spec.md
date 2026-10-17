## ADDED Requirements

### Requirement: Pro Max routing without fabricated allowance

Pro Max SHALL use the existing Pro reference capacity only for scheduling
heuristics, including constructed routing state and lease pressure. Other
unknown plans and explicitly zero-capacity states SHALL retain their
existing behavior. Reported Pro Max percentages SHALL establish quota
availability without requiring a known absolute allowance.

#### Scenario: Pro Max remains selectable
- **WHEN** an active Pro Max account reports an unexhausted weekly window
- **THEN** capacity-based and relative-availability routing do not treat it
  as a Free or zero-capacity account
- **AND** aggregate availability does not report exhaustion solely because
  its absolute allowance is unknown

### Requirement: Pro Max catalog entitlement

Pro Max SHALL receive Pro-family model eligibility fallback while retaining
its own plan identity. Account-specific model and service-tier catalog
evidence SHALL take precedence over plan fallback.

#### Scenario: Ultrafast requires the account catalog
- **WHEN** Pro Max advertises Astra Ultrafast and a Pro account advertises
  only Astra Priority
- **THEN** an Astra Ultrafast request may select Pro Max but not that Pro account
- **AND** a catalog that excludes the model or tier is not overridden by
  the local Pro Max label
