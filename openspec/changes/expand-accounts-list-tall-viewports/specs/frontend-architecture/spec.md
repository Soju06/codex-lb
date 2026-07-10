## ADDED Requirements

### Requirement: Accounts list uses available tall-viewport space

The Accounts page MUST size its scrollable account rows from the available
viewport height without imposing a smaller fixed height ceiling. The search,
filter, sort, help, and Add account controls MUST remain outside the rows scroll
region, and a list longer than the available region MUST continue to scroll
internally.

#### Scenario: Tall desktop viewport expands the rows region

- **WHEN** the Accounts page renders a long account list in a 1200px-tall desktop viewport
- **THEN** the account rows region is taller than 32rem
- **AND** the region uses the otherwise-empty space beneath the list controls

#### Scenario: Account pool still exceeds the available height

- **WHEN** the account rows require more space than the viewport-aware region provides
- **THEN** the rows remain internally scrollable through the final account
- **AND** the Add account action remains visible outside the scroll region
