## Purpose

Treat `reauth_required` as a refresh warning rather than proof that all account credentials are unusable. The canonical requirements live in the `account-routing` and `usage-refresh-policy` deltas.

## Constraints and failure modes

- Known-bad refresh material is never exchanged proactively.
- Forced refresh fails closed when fresh state still contains unchanged terminal material.
- Hard continuity never crosses accounts merely because refresh requires operator repair.
- A known-expired access token is rejected locally by selection and bridge reuse.
- Paused, deactivated, deleted, and security-ineligible accounts retain their existing exclusions.
- A token without a parseable expiry can still fail upstream once; that request then excludes the account and may fail over.

## Example

Account A's refresh token returns `token_expired`, but its stored access token still authorizes `/backend-api/codex/responses`. A remains `reauth_required`, routable, and owner of its bridge while proactive refresh is skipped. When the access token reaches its JWT expiry, selection and bridge reuse reject A locally; movable work may select account B, while hard account-owned continuity fails closed.
