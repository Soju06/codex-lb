## ADDED Requirements

### Requirement: Open dialogs leave page-level third-party surfaces usable

While a dashboard dialog built on the shared `Dialog` primitive is open, `<body>`
MUST NOT have pointer events disabled. Elements that a third party appends to the
document outside the dialog content, such as the suggestion list a password manager
draws next to a focused field, MUST receive clicks. The dialog's overlay MUST
remain the barrier that keeps the page behind the dialog from being clicked. A press
outside the dialog content MUST dismiss the dialog only when it lands on the overlay.
Pressing any other surface outside the content MUST NOT dismiss the dialog. Dialogs
SHALL keep focus trapping, hiding the rest of the document from assistive
technology, scroll locking, and Escape dismissal.

#### Scenario: Password-manager suggestion over an open dialog

- **GIVEN** an open dashboard dialog
- **AND** an element appended to `<body>` outside the dialog content and drawn above
  the overlay
- **WHEN** the person clicks that element
- **THEN** the element receives the click
- **AND** the dialog stays open

#### Scenario: Overlay still dismisses and blocks the page

- **GIVEN** an open dashboard dialog that allows outside dismissal
- **WHEN** the person presses the overlay
- **THEN** the dialog closes
- **AND** no element of the page behind the overlay receives the press
