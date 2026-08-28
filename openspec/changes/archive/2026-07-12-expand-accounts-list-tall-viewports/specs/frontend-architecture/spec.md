## ADDED Requirements

### Requirement: Accounts list uses available tall-viewport space

The Accounts page MUST size its scrollable account rows from the available
viewport height without imposing a smaller fixed height ceiling. The bound MUST
leave the page controls and fixed status bar visible, so account rows cannot
extend below the viewport even when the selected-account detail panel makes the
page taller. Optional controls MUST consume space from that bound according to
their rendered height. The search, filter, sort, help, and Add account controls
MUST remain outside the rows scroll region, and a list longer than the available
region MUST continue to scroll internally. When the controls and rows require
less height than the selected account details, the left card MUST remain
content-sized instead of stretching an empty bordered area to the bottom of the
details column.

#### Scenario: Tall desktop viewport expands the rows region

- **WHEN** the Accounts page renders a long account list in a 1200px-tall desktop viewport
- **THEN** the account rows region is taller than 32rem
- **AND** the region uses the otherwise-empty space beneath the list controls
- **AND** the final visible account row region ends above the fixed status bar

#### Scenario: Expanded help panel consumes rows space

- **WHEN** a user expands Windows OAuth Help above a long account list in a 1200px-tall desktop viewport
- **THEN** the help panel remains visible outside the rows scroll region
- **AND** the rows region shrinks by the rendered help-panel height
- **AND** the rows region still ends above the fixed status bar
- **AND** the final account remains reachable through internal scrolling

#### Scenario: Shorter account list does not stretch its card

- **WHEN** all account rows fit within the viewport-aware region
- **AND** the selected-account details are taller than the list controls and rows
- **THEN** the left card ends after the account rows and its normal bottom padding
- **AND** it does not render a large empty bordered area beneath the final account

#### Scenario: Account pool still exceeds the available height

- **WHEN** the account rows require more space than the viewport-aware region provides
- **THEN** the rows remain internally scrollable through the final account
- **AND** the Add account action remains visible outside the scroll region
