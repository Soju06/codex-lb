## Why

This fork should protect operator privacy without requiring prior knowledge of an opt-out environment variable. A fresh or upgraded installation must not transmit telemetry unless the operator explicitly enables it.

## What Changes

- Change the unresolved telemetry default from active to disabled.
- Preserve `CODEX_LB_TELEMETRY_ENABLED=true` as the explicit opt-in path.
- Ensure the disabled default performs no telemetry registration, activation, snapshot, or opt-out network request.
- Update the user-facing telemetry documentation to describe the fork's opt-in behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `telemetry`: Change default consent resolution from informed opt-out to explicit opt-in while preserving the existing payload allowlist and environment override.

## Impact

- Telemetry consent resolution and its unit tests.
- The telemetry OpenSpec contract and rendered documentation.
- No database migration, API shape, or new dependency is required.
