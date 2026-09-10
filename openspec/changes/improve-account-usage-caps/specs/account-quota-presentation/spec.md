## ADDED Requirements

### Requirement: Weekly usage caps apply only to weekly windows

Dashboard weekly pace calculations MUST reserve capacity for an account's configured weekly cap only when that account's normalized secondary window is the standard 10,080-minute weekly window. A positive secondary duration of any other length MUST use the provider's full capacity.

#### Scenario: Non-weekly secondary window ignores weekly cap

- **GIVEN** an account has a configured weekly usage cap
- **AND** its normalized secondary window has a positive duration other than 10,080 minutes
- **WHEN** the dashboard computes weekly pace
- **THEN** the account contributes its full provider capacity and remaining credits without a cap reserve

### Requirement: Capped quota bars do not rely on color alone

Every account quota bar that displays capacity reserved by a configured usage cap MUST render the reserved segment with a repeated diagonal hatch pattern in addition to its muted color. The pattern MUST remain visible in light and dark themes and MUST preserve the existing accessible cap label.

#### Scenario: Reserved capacity is hatched

- **GIVEN** an account quota window has a configured cap below 100 percent
- **WHEN** an account detail, account list, dashboard card, or dashboard list quota bar renders
- **THEN** the reserved segment uses repeated diagonal hatching
- **AND** its accessible label still identifies the cap and reserved percentage
