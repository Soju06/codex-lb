# frontend-architecture Specification (Delta)

## ADDED Requirements

### Requirement: Accounts page unified export action

The Accounts page SHALL render a single "Export" button in the account actions area. Clicking the export button SHALL open a modal dialog titled "Auth Export" with a format mode selector ("codex" / "opencode"). The page SHALL use a single API call to `POST /api/accounts/{id}/export/auth` before opening the modal, and SHALL pass the full response to the modal for display. No auto-download SHALL occur without user interaction in the modal.

#### Scenario: Single export button replaces dual buttons

- **WHEN** a user views the account actions for a selected account
- **THEN** exactly one "Export" button is visible
- **AND** no separate "Export OpenCode auth" button is present

#### Scenario: Export opens modal after API success

- **WHEN** a user clicks the "Export" button
- **THEN** the frontend calls `POST /api/accounts/{id}/export/auth`
- **AND** on success the "Auth Export" modal opens with the response data
- **AND** no file is downloaded until the user clicks Download in the modal

#### Scenario: Export error shows toast

- **WHEN** the `POST /api/accounts/{id}/export/auth` call fails
- **THEN** a toast notification shows the error message
- **AND** no modal opens
