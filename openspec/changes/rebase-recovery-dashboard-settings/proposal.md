## Why

Upstream dashboard settings migrations and native websocket event handling must
coexist with the HTTP bridge recovery branch after rebase.

## What Changes

- Join the existing recovery and upstream dashboard-timeout, routing, report-rollup, and automation claim-budget migration heads with metadata-only revisions.
- Preserve upstream native message parsing alongside recovery generation fences.
- Update migration graph and upgrade/downgrade regression coverage.

## Impact

No recovery defaults or historical migration parents change.
