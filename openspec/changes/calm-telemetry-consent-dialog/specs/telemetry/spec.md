# telemetry Delta

## MODIFIED Requirements

### Requirement: One-time consent dialog with exact payload preview

The dashboard MUST present a one-time consent dialog on first entry while consent is
`undecided`, and the dialog MUST make available the exact snapshot envelope the instance would
transmit at that moment. Preview and sender MUST use one shared envelope constructor. The
preview timestamp MUST record preview generation time as a representative current timestamp;
the actual send MUST regenerate that value at transmission time.

The dialog body MUST state what is collected and what is not in at most one short paragraph.
The exact envelope MUST be reachable from inside the dialog without navigating away, MUST NOT
be expanded by default, and MUST NOT stand between the operator and the decision actions: both
actions MUST be reachable without scrolling past the envelope.

A decision (enable or disable) MUST be persisted and the dialog MUST NOT be shown again after
any decision. The dialog MUST offer disabling with no fewer clicks than enabling, and the two
actions MUST be presented with equal visual prominence.

Dismissing the dialog without deciding MUST NOT change consent state, and MUST NOT cause the
dialog to be presented again on subsequent dashboard entries in the same browser. An operator
who dismissed the dialog MUST still be able to reach the same decision and the same envelope
from the settings view.

The consent API MUST build the preview only while the undecided dialog is eligible or when an
operator explicitly requests it for the settings view. The response MUST retain the `preview`
field and set it to `null` when the preview was not requested and is not dialog-relevant.

#### Scenario: Undecided operator sees payload preview

- **WHEN** an operator opens the dashboard while consent is `undecided`
- **THEN** a dialog shows a single short paragraph describing what is and is not collected,
  with equally prominent enable and disable actions
- **AND** the raw envelope is not rendered until the operator opens the disclosure control
- **AND** opening that control reveals the live snapshot JSON without leaving the dialog

#### Scenario: Decision is final until changed in settings

- **WHEN** the operator chooses disable in the dialog
- **THEN** consent persists as `disabled`, no snapshot is transmitted afterward, and the
  dialog never reappears

#### Scenario: Dismissal is remembered and changes nothing else

- **WHEN** the operator dismisses the dialog with Escape, the backdrop, or the close control
- **THEN** no consent decision is persisted and the reported consent state stays `undecided`
- **AND** re-entering the dashboard in the same browser does not present the dialog again
- **AND** the settings view still offers the toggle and the collected-data preview

#### Scenario: Decided consent status is a cheap read

- **WHEN** the dashboard reads consent after a persisted decision without requesting a preview
- **THEN** the response contains `preview: null` and no snapshot aggregation query runs

#### Scenario: Settings explicitly requests collected data

- **WHEN** the settings view requests a preview for any consent state
- **THEN** the response contains a current snapshot envelope built with the same schema as the sender
