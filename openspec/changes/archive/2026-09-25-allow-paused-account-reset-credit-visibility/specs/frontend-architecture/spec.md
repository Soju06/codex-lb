## ADDED Requirements

### Requirement: Paused account reset-credit counts remain inspectable

The selected paused account detail SHALL display the available reset-credit count returned by the dashboard read endpoint. It SHALL display the existing unavailable state on read failure when no successful query data is cached; an existing successful query value MAY remain visible during a failed refetch. Both usage-reset and banked-credit redemption actions MUST remain disabled while paused, even when the displayed count is positive. Existing count badges SHALL render available cached counts under their existing display settings.

#### Scenario: Paused account has reset credits
- **GIVEN** the selected paused account's count read returns two available credits
- **WHEN** the account detail renders
- **THEN** it SHALL show the count of two
- **AND** the reset action SHALL remain disabled

