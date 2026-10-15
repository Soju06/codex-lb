## Why

PR #2444's review identifies deleted SCIM messages, an outdated migration parent,
per-delta JSON parsing, and unrelated changes. The September 28 CI run reproduces
these regressions and a WebSocket terminal-clock regression.

## What Changes

- Restore all SCIM locale entries from current upstream main.
- Restamp the unmerged generation-evidence migration after main's single head.
- Count canonical output-delta events using the existing verbatim classifier,
  retaining JSON inspection for events outside the fast path.
- Restore the finalizer's routing terminal-clock fallback while keeping absent
  upstream timing evidence null in request logs.
- Remove source-usage hardening and the stream entrypoint relocation to separate PRs.
- Record the shared-state integration plan with #2112 and the unresolved metric
  definitions. No maintainer decision is assumed.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: preserve the no-per-delta-parse contract while
  collecting generation evidence and preserve the existing routing clock fallback.

## Impact

Proxy streaming and WebSocket timing, migration lineage, SCIM translations, tests
and contributor attribution. No new settings or dependencies. The metric policy
in #2444 remains pending maintainer confirmation before the PR can be ready.
