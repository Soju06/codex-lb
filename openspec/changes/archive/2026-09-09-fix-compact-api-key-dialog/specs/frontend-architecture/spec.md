## ADDED Requirements

### Requirement: API key create dialog remains usable in compact viewports

The dashboard API key create dialog MUST constrain its outer shell to the visible viewport. Its title, Close control, and Create action MUST remain fully visible at a 320x568 viewport, and every form field MUST remain reachable through exactly one internal vertical scroll region. The header and footer MUST remain outside that scroll region. The dialog SHALL retain its stacked compact layout, two-column desktop layout, shared Dialog primitive, field behavior, and overlay and Escape dismissal behavior.

#### Scenario: Compact viewport keeps primary controls visible

- **WHEN** an operator opens Create API Key from `/apis` at a 320x568 viewport
- **THEN** the dialog title, Close control, and Create action are fully inside the viewport
- **AND** the dialog shell does not extend above or below the viewport

#### Scenario: Compact form fields use one internal scroller

- **WHEN** the create form fields exceed the height available between the dialog header and footer
- **THEN** all General and Limits fields are reachable through one internal vertical scroll region
- **AND** the dialog header and footer remain outside the scroll region

#### Scenario: Larger compact and desktop layouts remain responsive

- **WHEN** an operator opens Create API Key at 390x844 or a desktop viewport
- **THEN** the dialog remains inside the viewport with its primary controls visible
- **AND** the form is stacked below the desktop breakpoint and uses two columns at the desktop breakpoint

#### Scenario: Existing dismissal behavior is preserved

- **WHEN** an operator presses Escape or activates the dialog overlay while no nested menu surface is active
- **THEN** the create dialog closes through the shared Dialog primitive
