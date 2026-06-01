## MODIFIED Requirements

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, assigned-account selection, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again, and the copy action MUST remain functional in secure and non-secure contexts.

#### Scenario: Create key with optional account scoping

- **WHEN** an admin opens the create API key dialog
- **THEN** the dialog shows the Assigned accounts picker
- **AND** leaving the picker at `All accounts` creates an unscoped key
- **AND** selecting one or more accounts creates a scoped key for only those accounts

#### Scenario: Create key and show plain key

- **WHEN** admin creates a key via the UI
- **THEN** a dialog shows the full plain key with a copy button and a warning message

#### Scenario: API key dialog copy fallback

- **WHEN** a user clicks Copy for the created API key inside the dialog
- **THEN** the copy operation succeeds using secure Clipboard API when available
- **AND** falls back to dialog-scoped `execCommand("copy")` when secure Clipboard API is unavailable
