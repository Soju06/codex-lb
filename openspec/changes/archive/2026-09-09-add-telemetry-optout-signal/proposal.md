## Why

Telemetry currently becomes silent when an operator disables it, so the collector cannot
distinguish an explicit rejection from an instance that stopped running. The wire contract also
needs the effective persisted consent state on snapshots so aggregate interpretation stays
accurate without weakening the environment kill switch.

## What Changes

- Add the active consent state (`undecided` or `enabled`) to every snapshot payload.
- Send one final signed opt-out notification for each dashboard-driven transition from active
  telemetry to inactive telemetry.
- Preserve absolute silence for the `CODEX_LB_TELEMETRY_ENABLED=false` environment path and
  isolate opt-out transmission failures from the settings API response.
- Explain the additive wire behavior in the telemetry settings UI and published telemetry docs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `telemetry`: Extend the outbound allowlist and consent behavior with snapshot consent and the
  decision-time opt-out notification.

## Impact

- Telemetry schemas, snapshot construction, scheduler and preview callers, sender, and settings
  API transition handling.
- Telemetry unit tests and wire-contract allowlists.
- Dashboard telemetry consent copy and component tests.
- Published telemetry payload documentation.
