## Why

Trusted-header dashboard authentication currently selects the first configured identity field when a trusted proxy forwards duplicates. An append-style proxy can therefore preserve an attacker-supplied value and let field order determine the authenticated admin actor.

## What Changes

- Require exactly one configured trusted identity field before creating a trusted-header dashboard principal.
- Treat duplicate identity fields as ambiguous even when their values match or only one value is non-empty.
- Return the existing `proxy_auth_required` rejection envelope without producing an authenticated identity.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Define fail-closed cardinality for trusted-header dashboard identities.

## Impact

Affected areas are trusted-header identity parsing, the dashboard authentication boundary, focused integration coverage, and the admin-auth contract. Proxy trust configuration, password fallback sessions, untrusted-peer scrubbing, and identity normalization remain unchanged.
