## Why

Issue #787 reports that after OAuth token invalidation and account re-import,
operators can end up with two account rows for the same real email: one stale
row with a revoked refresh token and one fresh row. The dashboard API did not
surface that relationship, so operators had to manually group `/api/accounts`
responses to find stale/fresh pairs.

## What Changes

- Add `isEmailDuplicate` to each `AccountSummary` returned by `GET /api/accounts`.
- Set the field to `true` for all account rows whose real, non-placeholder email
  and ChatGPT account identity pair appears more than once in the same response.
- Keep same-email rows with different ChatGPT account identities unflagged so
  valid workspace/org-separated accounts do not show as stale duplicates.
- Exclude missing/blank email values and the legacy `DEFAULT_EMAIL`
  placeholder (`unknown@example.com`) from duplicate detection.
- Mirror the field in the frontend account schema as optional so existing
  fixtures and literal account summaries keep working.

## Impact

- Existing account payload consumers continue to work; the new field defaults to
  `false`.
- Dashboard consumers can highlight duplicate real-email rows without falsely
  flagging malformed imports that carry the shared placeholder email.
