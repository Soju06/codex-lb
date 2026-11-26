## Why

PR #2325 adds optional progress for #1636. A closed stderr reader currently aborts retag, including after backup or mutation.

## What Changes

- Disable progress after BrokenPipeError and finish the confirmed retag.
- Preserve all file, backup, verification and summary behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `runtime-portability`: tolerate a closed structured-progress reader.

## Impact

CLI progress callback and public CLI regression tests. No settings, runtime changes or SQLite access changes.
