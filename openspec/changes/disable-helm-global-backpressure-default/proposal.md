## Why

The Helm chart defaults `config.backpressureMaxConcurrentRequests` to `200`,
so a default install sets `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS=200`.
A positive value installs one process-wide semaphore across every traffic
class, which contradicts the application default of `0` (disabled) and the
split-by-class admission contract.

## What Changes

- Change the chart default for `config.backpressureMaxConcurrentRequests`
  from `200` to `0` so default Helm installs leave global backpressure off.
- Keep the existing ConfigMap wiring and allow operators to set a positive
  override when they want a global cap.
- Add a real Helm-render regression that the default ConfigMap value is
  `"0"` and that an explicit override of `37` renders `"37"`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Default Helm installs MUST leave the global
  backpressure cap disabled (`0`). A positive operator override remains
  allowed; the default MUST NOT install one semaphore across traffic classes.

## Impact

Only `deploy/helm/codex-lb/values.yaml` production behavior changes, plus
OpenSpec artifacts and the Helm-render regression. Runtime Python, global
middleware, README, CHANGELOG, settings docs, and other chart values stay
unchanged. Existing Helm releases that already override the value keep that
override; un-overridden upgrades stop applying the unintended global cap.
