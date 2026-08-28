## Why

A cancelled limited-key `/v1/audio/transcriptions` request can leave its committed usage reservation in `reserved` state until stale reclamation, consuming quota after request ownership has ended. Cancellation must release that reservation immediately through the established cancellation-safe cleanup mechanism.

## What Changes

- Release the owned source-audio-transcription usage reservation when upstream forwarding is cancelled.
- Preserve the original cancellation after cleanup and keep existing success, forwarding-error, missing-usage, and settlement behavior unchanged.
- Add deterministic route-level regression coverage that cancels only after upstream audio forwarding begins and verifies the exact reservation is released and its reserved quota is restored.
- Keep non-stream Responses, streaming chat, request-log taxonomy, and cleanup abstractions outside this scoped fix.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Require immediate, exactly-once release of an owned source-audio-transcription reservation when request cancellation interrupts upstream forwarding.

## Impact

The change is limited to the source-routed `/v1/audio/transcriptions` helper in `app/modules/proxy/api.py`, its focused route-level integration coverage, and the API-key reservation contract. It adds no public API, setting, dependency, migration, or dashboard change.
