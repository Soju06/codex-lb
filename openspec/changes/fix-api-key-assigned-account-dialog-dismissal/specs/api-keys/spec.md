## MODIFIED Requirements

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, assigned-account selection, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again, and the copy action MUST remain functional in secure and non-secure contexts.

The create and edit dialogs SHALL expose an `Apply to codex /model` checkbox directly below `Allowed models`. The checkbox SHALL default to unchecked for new keys and SHALL edit the stored API key value for existing keys.

The Assigned accounts picker inside API key create and edit dialogs MUST NOT dismiss the parent dialog while the operator is selecting accounts or moving focus back to other controls in the same dialog. The pending assigned-account selection MUST remain saveable through the dialog's Save action.

#### Scenario: Edit assigned accounts remains saveable

- **WHEN** an admin opens the edit API key dialog
- **AND** selects one or more accounts from the Assigned accounts picker
- **AND** clicks another control in the same dialog without first pressing Escape
- **THEN** the edit dialog remains open
- **AND** clicking Save submits the selected assigned account IDs
