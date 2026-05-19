## MODIFIED Requirements

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again.

When editing a key that can be scoped to assigned accounts, the Assigned accounts picker SHALL show each account option with its display name or email, status, plan label, and the remaining primary and secondary availability derived from the existing account summary usage fields. The picker SHALL surface only the general account availability windows and SHALL NOT include model-specific additional quota badges inline.

#### Scenario: Edit key and review assigned-account availability

- **WHEN** an admin opens the API key edit dialog and expands the Assigned accounts picker
- **THEN** each account row shows its identity, status, plan label, and remaining `5h` / `7d` availability when those usage values are available

#### Scenario: Assigned-account picker stays focused on base availability

- **WHEN** an account also has model-specific additional quota data
- **THEN** the Assigned accounts picker omits those additional quota badges and continues to show only the account's general `5h` / `7d` availability
