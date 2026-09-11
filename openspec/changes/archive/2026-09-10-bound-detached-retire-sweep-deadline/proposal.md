## Why

Issue #2149 identifies request finalization waiting up to five seconds for each detached session lock. Several busy sessions multiply that delay.

## What Changes

- Give each request's detached retirement sweep one five-second monotonic deadline.
- Pass only remaining time to each lock wait and stop starting attempts after expiry.
- Retain deferred sessions for existing lifecycle cleanup and report the skipped count.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: bound detached retirement lock waits across the sweep.

## Impact

HTTP bridge request finalization, its regression tests, and the owning spec. No configuration or dependency changes.
