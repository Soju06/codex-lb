## ADDED Requirements

### Requirement: Account usage-cap controls and markers

The Accounts page SHALL offer independent optional 5h and weekly usable-quota-percentage cap controls for applicable windows, with existing configured values removable when a window disappears. Enabled caps SHALL expose a visually distinct disable button. Read-only users SHALL NOT be able to save changes. Individual account remaining-quota bars SHALL show reserved quota as a solid gray segment and show usable remaining quota in the value. Dashboard account bars SHALL use the same segment while keeping the shorter parenthetical value. Aggregate 5h and weekly credit donuts and weekly credit pace calculations SHALL subtract reserved capacity. Provider remaining percentages SHALL remain unchanged; monthly and additional-quota bars SHALL NOT display cap segments.

#### Scenario: Reserved quota is visible
- **WHEN** a displayed 5h account bar has a configured cap of 80 percent used
- **THEN** the first 20 percent of its remaining-quota track is a solid gray unavailable segment
- **AND** the Accounts page value identifies that 20 percent as unusable
- **AND** aggregate credits exclude the reserved capacity

#### Scenario: Independent controls
- **WHEN** an operator enables only the weekly cap
- **THEN** the weekly threshold is editable and saved independently
- **AND** the 5h bar has no cap marker
