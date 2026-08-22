## Purpose and scope

This change treats `reauth_required` as a warning about refresh-token exchange, not proof that the stored access token is unusable. It applies to ordinary proxy requests and access-token-authenticated supporting operations. It does not add access-token-only import support or weaken `deactivated` handling.

## Rationale

Refresh and access credentials have different lifetimes and failure modes. A refresh endpoint can reject a refresh token while the current access token still authorizes requests. Keeping those accounts in the request pool preserves capacity and owner-bound continuity until upstream demonstrates that the access token itself is unusable.

A separate status was considered but would add schema, migration, dashboard, and compatibility cost without changing the operator action: reauthentication is still required.

## Constraints

- Proactive and background refresh MUST NOT exchange known-bad refresh material from a `reauth_required` row.
- Forced refresh after an upstream rejection MUST re-read fresh state and fail closed on unchanged terminal material.
- File, previous-response, and bridge ownership MUST NOT cross accounts merely because refresh exchange requires reauthentication.
- `deactivated`, paused, deleted, and security-ineligible accounts retain their existing hard exclusions.

## Failure modes and operations

An access token that has also expired can cause the account to be tried once by each independent request. Within a request, permanent forced-refresh failure excludes it from the remaining retry pool, so movable work can fail over. Operators see the persistent `reauth_required` status and can repair the account through the existing reauthentication flow.

During a rolling deployment, an older replica still uses the former hard-block policy and can clear affinity when it commits `reauth_required`. Replace old replicas promptly; current replicas clear stale local routing overlays when the committed status snapshot converges.

## Example

Account A's refresh token returns `token_expired`, but its stored access token still succeeds on `/backend-api/codex/responses`. The account becomes `reauth_required`, remains eligible for a later request and keeps its bridge owner, while proactive refresh is skipped. If upstream later returns 401, forced refresh fails closed without re-exchanging the dead refresh token; that request excludes A and may fail over to account B.
