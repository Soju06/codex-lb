## ADDED Requirements

### Requirement: Model source dialogs remain usable in compact viewports

The dashboard model source create and edit dialogs MUST constrain their outer shell
to the visible viewport. Each dialog's title, Close control, and submit action MUST
remain fully visible at a 320x568 viewport, and every form field MUST remain
reachable through exactly one internal vertical scroll region. The dialog header and
submit footer MUST remain outside that scroll region. Both dialogs SHALL retain their
existing fields, capability toggles, validation, submission behavior, shared Dialog
primitive, and overlay and Escape dismissal behavior.

#### Scenario: Compact viewport keeps the submit action reachable

- **WHEN** an operator opens Add source from the advanced section of `/settings` at a
  320x568 viewport
- **THEN** the dialog title, Close control, and Create action are fully inside the
  viewport
- **AND** the dialog shell does not extend above or below the viewport

#### Scenario: Form fields use one internal scroller

- **WHEN** the model source form fields exceed the height available between the
  dialog header and footer
- **THEN** every field from the source name through the default reasoning effort is
  reachable through one internal vertical scroll region
- **AND** the dialog header and submit footer remain outside the scroll region

#### Scenario: Larger compact and desktop viewports remain bounded

- **WHEN** an operator opens the create dialog at 390x844 or a desktop viewport
- **THEN** the dialog remains inside the viewport with its primary controls visible

#### Scenario: Edit dialog matches the create dialog shell

- **WHEN** an operator opens Edit on an existing model source at a compact viewport
- **THEN** the edit dialog applies the same viewport-bounded shell, one internal
  scroll region, and persistently visible Save action

#### Scenario: Existing dismissal behavior is preserved

- **WHEN** an operator presses Escape or activates the dialog overlay while no nested
  menu surface is active
- **THEN** the dialog closes through the shared Dialog primitive
