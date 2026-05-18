# Change Proposal

API keys can currently be scoped to assigned accounts only after creation. That forces operators into a two-step workflow even when they already know the target accounts at create time.

## Changes

- Allow `POST /api/api-keys` to accept an optional assigned-account list during creation.
- Add the Assigned accounts picker to the API key create dialog, reusing the existing account selection UI.
- Preserve the current default behavior: when no accounts are selected, the new key applies to all accounts.
