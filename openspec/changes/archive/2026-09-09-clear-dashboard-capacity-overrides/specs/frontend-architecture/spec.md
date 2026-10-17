# frontend-architecture

## ADDED Requirements

### Requirement: Routing settings expose inherited capacity overrides

The dashboard routing settings MUST display the effective value and the raw
override state for each of the four account-capacity settings. A `NULL` raw
override MUST render as an empty inheritable input with the effective value
shown as a hint. Clearing that input MUST submit explicit `null`; entering a
nonnegative integer, or an integer from 0 to 100 for the fair-share threshold,
MUST submit an override value. The dashboard MUST prevent invalid values from
being saved and MUST retain the existing stream-recovery-reserve validation.
When validating a clear of a stored override, the dashboard MUST use the
environment baseline value rather than the currently effective override value.

#### Scenario: Inherited capacity is visible

- **GIVEN** a capacity override is `NULL`
- **WHEN** routing settings render
- **THEN** the input is empty
- **AND** the effective environment value is shown as the inherit hint

#### Scenario: Clearing an override round-trips as null

- **GIVEN** a routing setting currently has a numeric override
- **WHEN** the operator empties the field and saves
- **THEN** the update payload contains the field with explicit `null`
- **AND** a subsequent GET shows the effective value and a `null` override

#### Scenario: Unedited fields are preserved

- **GIVEN** one capacity field is cleared and sibling fields have overrides
- **WHEN** the operator saves
- **THEN** the payload does not pin or clear the sibling fields

#### Scenario: Capacity validation remains enforced

- **WHEN** the operator enters a negative value, a non-integer, a fair-share
  value outside 0-100, or a recovery reserve above the effective stream cap
- **THEN** the dashboard prevents the save

#### Scenario: Clearing a stream limit validates against environment capacity

- **GIVEN** a stored stream-limit override of 24, a stored recovery reserve of
  3, and an environment stream limit of 2
- **WHEN** the operator clears only the stream-limit input
- **THEN** the dashboard prevents the save
