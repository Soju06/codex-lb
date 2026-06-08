## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable
account list, import button, and add account button; right panel with selected
account details including usage, token info, and actions
(pause/resume/delete/re-authenticate). Account status displays and filters
SHALL distinguish `reauth_required` accounts from `deactivated` accounts:
`reauth_required` means the local credential/session must be refreshed by
operator re-authentication, while `deactivated` means the upstream account is
disabled, suspended, deleted, or explicitly deactivated. The browser OAuth
stage SHALL show an authorization URL with a copy action that remains
functional in secure and non-secure contexts.

The Accounts page SHALL also allow exporting a selected account as an
OpenCode-compatible `auth.json` payload with explicit raw-token warnings.

#### Scenario: Re-authentication-required account is labeled separately

- **WHEN** an account summary has `status = "reauth_required"`
- **THEN** the account list and account detail status badge show
  `Re-auth required`
- **AND** the account can be found with the status filter for
  `reauth_required`
- **AND** the account detail exposes the re-authenticate action
- **AND** the account detail does not expose pause or resume actions that could
  bypass re-authentication
- **AND** the account list and account detail do not expose routing-policy
  controls that imply the account is selectable while operator recovery is
  required
