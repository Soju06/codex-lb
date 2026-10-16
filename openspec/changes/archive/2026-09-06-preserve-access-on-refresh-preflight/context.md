## Context and limits

An OAuth refresh failure and an access-token rejection are distinct events.
Existing upstream routing already permits warning-state accounts to serve
ordinary requests until known access-token expiry. This change closes the
first-request preflight gap without changing that policy or hiding the warning.

For example, a stale imported credential can have a future access-token expiry
while its refresh token returns `refresh_token_invalidated`. The first native
Responses request should reach upstream with the stored access token, just as
the next request would once it observes `reauth_required`.

The recovery belongs outside the shared refresh task: an ordinary caller and
a forced-refresh caller may share the same exchange but must not share a
successful fallback. The forced caller may have already observed an upstream
401, so its error must remain an error. The fresh database read also avoids
depending on mutations to another caller's ORM snapshot.

The persisted warning must match the refresh error's canonical reason. The
terminal-status guard can synthesize a refresh error for an already-invalidated
session; checking the stored reason prevents that synthesized error from
accidentally authorizing this narrower preflight recovery.

Only explicit refresh-credential failures qualify: `refresh_token_expired`,
`refresh_token_reused`, `refresh_token_invalidated`, `invalid_refresh_token`,
and `invalid_grant`. Session/account invalidation and transport or persistence
failures retain their existing handling. No extra usage or model probe is sent.
The actual upstream request remains the authority on token acceptance; a JWT
expiry is only a local pre-dispatch check, not proof of authentication.

This does not establish that Advanced Account Security caused the reported
refresh rejection, nor that any particular previously rejected credential
remained valid. It cannot prevent reauthentication when OpenAI actually expires
or revokes the access token.

## Verification

- The native Responses regression failed on the unmodified base with
  `response.failed` before dispatch and passes with this change.
- The affected auth, guardian, refresh-claim, balancer and Responses suites
  passed: 578 passed, 3 pre-existing skips.
- Repository lint, formatting, type, architecture and cancellation checks passed.
- Strict change validation and ordinary all-spec validation passed (58 specs).
  Global strict validation reports existing placeholder-purpose warnings in
  unrelated capabilities; they are not changed by this patch.
