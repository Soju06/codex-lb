## Why

Issue #1919 reports repeated reauthentication with Advanced Account Security.
The merged `keep-reauth-required-access-routable` change already preserves
access-token routing after a refresh warning. However, the ordinary request
that first discovers a permanent refresh-credential failure still raises from
its freshness preflight before trying the stored access token. A single-account
or owner-bound request can fail even though its access token remains usable.

## What Changes

- Let an ordinary, non-forced freshness preflight continue on the same account
  after a refresh-credential-only failure when the freshly persisted row is
  `reauth_required` and its access token has a known future expiry.
- Keep the refresh warning and credentials unchanged; upstream still validates
  the actual request. Forced refresh after an upstream rejection still fails.
- Preserve singleflight, cross-replica claims, guarded writes, and cancellation.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`: Separate the caller's preflight recovery from the
  shared refresh result and constrain when a stored access token may be tried.

## Impact

Backend authentication preflight and regression tests only. No new setting,
network probe, dependency, schema, migration, or installation step. This is a
partial mitigation for #1919, not a way to extend or override OpenAI sessions.
