### ADDED Requirement: Dashboard density limits

The Dashboard page SHALL render donut legends in a vertically scrollable list that shows up to 5 legend rows before scrolling. The Dashboard page SHALL render the account cards grid inside a vertically scrollable container with hidden scrollbars and a viewport that shows no more than 2 rows of accounts.

#### Scenario: Donut legend viewport shows five rows

- **WHEN** a donut chart has more than 5 legend items
- **THEN** the legend list shows 5 rows before scrolling

#### Scenario: Account cards viewport shows two rows

- **WHEN** the Dashboard page renders more than 2 rows of account cards
- **THEN** the account cards container scrolls vertically
- **AND** the scrollbar remains visually hidden
- **AND** only 2 rows are visible without scrolling

