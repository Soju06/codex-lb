# Verification

## Regression evidence

Before the fix, the new real Responses-route test observed a health write with
the API-key reservation still `reserved`. After routing initial HTTP 401 to the
existing forced-refresh handler, the test observes original token, refreshed
token on the same account, and failover to another account. The reservation is
`finalized` with two input tokens and one output token before the revoked
account is persisted as `reauth_required`. Both deterministic failover settings
are covered.

WebSocket route tests cover `error` and `response.failed` with `token_revoked`
and no authentication type. Pre-created requests keep one request state and
one upstream send per refresh/failover connection; retired sockets close.
Terminal-ID and prior-`response.created` cases preserve the error, record
revoked-token health, and do not replay the request.

## Checks

- 20 Responses/WebSocket integration auth, refresh, revoked-token, and settlement cases passed.
- 110 proxy unit auth, refresh, and revoked-token cases passed.
- `make lint typecheck` passed, including all four architecture checks.
- Focused lint/format checks passed after updating the existing recovered-401 unit assertion.
- CI-pinned `@fission-ai/openspec@1.11.0` strict validation passed for the change and all 65 main specs.
- The globally installed OpenSpec 1.4.1 rejected pre-existing main-spec formatting; validation uses the CI-pinned version.

The existing owner-bound burst regression now expects no health write for its
recovered initial 401, while still proving same-owner backoff and no failover.
