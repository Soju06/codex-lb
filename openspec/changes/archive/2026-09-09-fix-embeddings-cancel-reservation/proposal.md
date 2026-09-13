## Why

A cancelled limited-key `/v1/embeddings` request can leave its committed usage reservation in `reserved` state until stale reclamation, consuming quota after request ownership has ended. Cancellation must release that reservation immediately through the same cancellation-safe cleanup mechanism used by adjacent source-chat paths.

## What Changes

- Release the owned source-embeddings usage reservation when upstream forwarding is cancelled.
- Preserve the original cancellation exception after cleanup and keep existing success, forwarding-error, and usage settlement behavior unchanged.
- Add deterministic route-level regression coverage that cancels only after upstream forwarding begins and verifies the exact reservation reaches `released` state.
- Record source audio transcription as an adjacent same-shape risk without changing that route in this scoped fix.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Require immediate, exactly-once release of an owned source-embeddings reservation when request cancellation interrupts upstream forwarding.

## Impact

The change is limited to the source-routed `/v1/embeddings` helper in `app/modules/proxy/api.py`, its focused integration coverage, and the API-key reservation contract. It adds no public API, setting, dependency, migration, or dashboard change.
