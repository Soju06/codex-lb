## Purpose

Treat `reauth_required` as a refresh warning rather than proof that all account credentials are unusable. The canonical requirements live in the `account-routing` and `usage-refresh-policy` deltas.

## Constraints and failure modes

- Known-bad refresh material is never exchanged proactively.
- Forced refresh fails closed when fresh state still contains unchanged terminal material.
- Hard continuity never crosses accounts merely because refresh requires operator repair.
- Paused, deactivated, deleted, and security-ineligible accounts retain their existing exclusions.
- An access token that is also expired can fail once per independent request; that request then excludes the account and may fail over.

## Example

Account A's refresh token returns `token_expired`, but its stored access token still authorizes `/backend-api/codex/responses`. A remains `reauth_required`, routable, and owner of its bridge while proactive refresh is skipped. If upstream later rejects the access token, forced refresh does not retry the dead refresh token; the current movable request excludes A and may select account B.
