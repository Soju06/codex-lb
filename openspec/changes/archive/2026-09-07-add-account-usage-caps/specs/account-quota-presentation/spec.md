## ADDED Requirements

### Requirement: Account usage-cap controls and markers

The Accounts page SHALL offer independent optional 5h and weekly consumed-percentage cap controls for applicable windows, with existing configured values removable when a window disappears. Read-only users SHALL NOT be able to save changes. Individual account remaining-quota bars on the Accounts and Dashboard pages SHALL indicate the configured cap at `100 - cap` percent remaining with accessible descriptive text. Provider remaining percentages SHALL remain unchanged; monthly and additional-quota bars SHALL NOT display these cap markers.

#### Scenario: Reserved quota is visible
- **WHEN** a displayed 5h account bar has a configured cap of 80 percent used
- **THEN** its cap marker appears at 20 percent remaining
- **AND** its accessible description identifies the cap and remaining threshold

#### Scenario: Independent controls
- **WHEN** an operator enables only the weekly cap
- **THEN** the weekly threshold is editable and saved independently
- **AND** the 5h bar has no cap marker
