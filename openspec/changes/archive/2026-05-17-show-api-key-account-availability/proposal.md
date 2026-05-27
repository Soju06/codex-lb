# Change Proposal

The API key edit dialog already lets operators scope a key to specific assigned accounts, but the picker currently shows only account identity. That makes it hard to choose accounts with enough remaining base capacity without switching back to the Accounts or Dashboard views.

## Changes

- Show each assigned-account option with its account status, plan label, and remaining primary/secondary availability in the picker.
- Reuse the existing account summary data already returned to the dashboard SPA; do not add new API fields.
- Keep the picker concise by limiting the inline chips to the general 5h and 7d availability instead of model-specific additional quota badges.
