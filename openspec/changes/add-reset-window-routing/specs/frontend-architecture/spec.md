## ADDED Requirements

### Requirement: Reset-window routing setting UI
The dashboard routing settings UI SHALL expose a control for the earlier-reset
preference window whenever earlier-reset routing preference is configurable. The
control SHALL allow only `primary` and `secondary` values and SHALL submit the
selected value using the settings API field `preferEarlierResetWindow`.

#### Scenario: Operator selects primary reset window
- **GIVEN** the routing settings UI is open
- **WHEN** the operator selects `primary` as the earlier-reset window
- **THEN** the settings update payload includes `preferEarlierResetWindow: "primary"`

#### Scenario: Imported settings preserve reset-window preference
- **GIVEN** an imported settings payload includes `preferEarlierResetWindow`
- **WHEN** the settings import is applied
- **THEN** the imported value is sent to the backend instead of being dropped
