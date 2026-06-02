## Why

Re-authenticating an account whose previous refresh token was revoked can create a duplicate local account row when the new OAuth payload is persisted through the normal account upsert path. That is confusing for operators because the same upstream ChatGPT identity appears twice, often with one stale deactivated row and one fresh active row.

The account lifecycle needs a stable identity rule for re-authentication: if the upstream ChatGPT account identity is known and already exists locally, reauth should refresh that existing local row instead of allocating a duplicate `__copyN` account id.

## What Changes

- Define re-authentication merge semantics for OAuth token persistence by upstream ChatGPT identity.
- Reuse the existing local account row when a new OAuth token payload carries the same `chatgpt_account_id`.
- If duplicate local rows already exist for that upstream identity, repoint dependent rows (usage history, request logs, sticky sessions, warmup state, API key assignments, HTTP bridge sessions, additional usage history) to the canonical row before deleting duplicates.
- Keep import-without-overwrite behavior separate: duplicate imports can still create separate local rows, but reauth is an identity refresh of an already-known upstream account.
- Preserve concurrent safety so simultaneous reauth completions for the same upstream identity cannot create duplicate rows.

## Capabilities

### New Capabilities

### Modified Capabilities
- `frontend-architecture`: account management now defines that OAuth re-authentication refreshes an existing row by upstream ChatGPT identity.

## Impact

- Affects OAuth token persistence through `app/modules/oauth/service.py`.
- Affects account repository identity-merge behavior.
- Adds regression coverage for concurrent identity-based reauth persistence.
