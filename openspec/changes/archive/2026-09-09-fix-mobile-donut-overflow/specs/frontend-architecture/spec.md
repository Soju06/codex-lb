## ADDED Requirements

### Requirement: Dashboard usage content remains contained on mobile

The dashboard MUST keep the document width within the visible viewport at 320x568 and 390x844. Both usage donut cards, their horizontal content rows, fixed-size charts, and legends MUST remain within the dashboard content width. The request table MAY exceed the viewport width only inside its own horizontal scroller and MUST NOT widen the document. The dashboard SHALL preserve the two-column usage layout and page-level containment at a 1440x900 desktop viewport.

#### Scenario: Usage donuts fit the smallest supported mobile viewport

- **WHEN** an authenticated operator opens `/dashboard` at 320x568 with account usage in both quota windows
- **THEN** the document does not scroll horizontally
- **AND** both usage donut cards and their rendered content remain within the dashboard content width
- **AND** the account-section heading and summary remain within the document width

#### Scenario: Usage donuts fit the larger mobile viewport

- **WHEN** the same dashboard data renders at 390x844
- **THEN** the document does not scroll horizontally
- **AND** both usage donut cards remain within the dashboard content width

#### Scenario: Request table keeps local horizontal scrolling

- **WHEN** the request table's columns require more width than the mobile content area
- **THEN** the table remains wider than its local horizontal scroller
- **AND** the scroller contains that width without widening the document

#### Scenario: Desktop layout remains contained

- **WHEN** the same dashboard data renders at 1440x900
- **THEN** the usage donuts render in two columns
- **AND** the document remains horizontally contained
- **AND** the request table retains its local horizontal scrolling when its columns exceed the available section width
