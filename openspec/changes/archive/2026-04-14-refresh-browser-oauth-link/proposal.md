## Why
The Accounts page OAuth dialog currently generates a browser PKCE authorization link only when the user first clicks `Start sign-in`. Because the link is single-use, operators who want to sign in multiple accounts in sequence must leave the current browser step and start over to mint another link. That adds unnecessary friction to a common dashboard workflow.

## What Changes
- Add an explicit refresh action to the browser PKCE stage of the Accounts OAuth dialog.
- Reuse the existing browser OAuth start flow so refreshing creates a fresh authorization URL without forcing the user to leave the dialog.
- Cover the refreshed-link behavior with frontend tests.

## Impact
- Affects the Accounts page OAuth dialog in the frontend.
- Reuses the existing `/api/oauth/start` browser flow and its PKCE/state regeneration.
- No API contract changes are required.
