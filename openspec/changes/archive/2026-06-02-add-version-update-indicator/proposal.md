## Why

Operators can see the running codex-lb version in the dashboard footer, but there is no visible signal when the deployed version falls behind the latest GitHub release. This makes small self-hosted deployments easy to leave stale.

## What Changes

- Add a backend runtime version status endpoint that compares the running app version with the latest GitHub release.
- Cache GitHub release lookups and degrade silently when the lookup fails.
- Show a compact update-available icon beside the footer version when a newer release exists.
- Link the icon to the latest GitHub release notes.

## Impact

- New dashboard-auth protected support API under `/api/runtime/version`.
- New outbound HTTP call to GitHub releases API with in-process caching.
- Footer UI gets one optional icon and tooltip when update information is available.
