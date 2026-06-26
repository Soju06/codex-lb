## Why

The live dashboard can show `Response schema mismatch` when recent request-log rows include Codex-native request kinds such as `prewarm`. The backend request-log contract exposes `requestKind` as a string because proxy and compact flows may preserve additional audited request classes, but the frontend schema was limited to three values.

## What Changes

- Allow dashboard request-log rows to parse any string `requestKind`, defaulting omitted values to `normal`.
- Keep friendly labels for known non-normal kinds, including `prewarm` and `compaction`.
- Add regression coverage for request-log payloads containing backend-preserved request kinds.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Request-log rendering remains resilient when backend request-log rows contain newly preserved request-kind strings.

## Impact

- Frontend request-log response schema and recent-request row labels.
- Frontend schema regression coverage.
