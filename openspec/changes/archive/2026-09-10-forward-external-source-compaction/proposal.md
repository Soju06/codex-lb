## Why

Explicit compact requests for a registered external Responses model enter subscription account selection. With no subscription accounts they return `503 no_accounts`, although the configured source can serve `/responses/compact`.

## What Changes

- Route explicit compact requests to their configured Responses source when no subscription continuity or uploaded-file owner requires native routing.
- Preserve source authentication, payload history, admission, usage settlement, cancellation and error reporting.
- Keep native compact ownership and serialization unchanged. Unsupported source compaction returns the source's error without native fallback.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: explicit compact routing for external Responses sources.

## Impact

Both `/v1/responses/compact` and `/backend-api/codex/responses/compact`, the shared source dispatcher and the source HTTP transport. No catalog discovery dependency, provider translation, schema migration, setting or frontend change.
