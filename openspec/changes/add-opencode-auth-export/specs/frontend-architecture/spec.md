## MODIFIED Requirements

### Requirement: Accounts dashboard page
The Accounts page SHALL allow users to list imported accounts, inspect usage and authentication status, import `auth.json` files, start OAuth account onboarding, pause/resume accounts, delete accounts, and export a selected account as an OpenCode-compatible `auth.json` payload.

#### Scenario: Export selected account from dashboard
- **WHEN** a user clicks the OpenCode export action for a selected account
- **THEN** the dashboard requests a per-account export from the backend
- **AND** shows copy/download controls for the official OpenCode `auth.json` payload
- **AND** warns that the payload contains raw account tokens
