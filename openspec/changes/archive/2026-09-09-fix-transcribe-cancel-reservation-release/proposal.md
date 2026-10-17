## Why

A cancelled subscription-backed transcription request can leave its committed API-key usage reservation in `reserved` state until stale reclamation, consuming quota after request ownership has ended. Cancellation must release that reservation immediately through the established cancellation-safe cleanup mechanism.

## What Changes

- Release the owned subscription-backed transcription usage reservation when upstream forwarding is cancelled.
- Preserve the original cancellation after cleanup and keep existing success, forwarding-error, and response behavior unchanged.
- Add deterministic route-level regression coverage that cancels only after transcription forwarding begins and verifies the release completes exactly once and the reservation quota is restored.
- Cover both `/backend-api/transcribe` and `/v1/audio/transcriptions` through their shared request helper without changing response shape or billing behavior.

## Capabilities

### New Capabilities

- `api-keys`: Require immediate, exactly-once release of an owned subscription-backed transcription reservation when request cancellation interrupts upstream forwarding.

### Modified Capabilities

None.

## Impact

The change is limited to the shared subscription-backed transcription helper in `app/modules/proxy/api.py`, its focused route-level integration coverage, and the API-key reservation contract. It adds no public API, setting, dependency, migration, or dashboard change.
