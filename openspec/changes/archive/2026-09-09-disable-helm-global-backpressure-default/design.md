## Context

Application `Settings.backpressure_max_concurrent_requests` defaults to `0`.
`app.main` installs `BackpressureMiddleware` only when that value is greater
than zero. A positive value is one process-wide semaphore shared by HTTP,
websocket, compact, and dashboard traffic.

The Helm chart already renders
`CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS` from
`config.backpressureMaxConcurrentRequests`, but `values.yaml` currently
defaults that field to `200`. A default Helm install therefore turns the
global cap on and can starve one traffic class from another.

## Goals / Non-Goals

**Goals:**

- Align the chart default with the application default of `0`.
- Keep the existing ConfigMap key and operator override path.
- Prove default `"0"` and override `"37"` through real Helm rendering.

**Non-Goals:**

- Changing runtime Python, middleware, or Settings defaults.
- Removing the optional global cap.
- Editing README, CHANGELOG, settings docs, or unrelated chart values.
- Changing per-class bulkhead or account-local admission limits.

## Decisions

Change only `deploy/helm/codex-lb/values.yaml`
`config.backpressureMaxConcurrentRequests` from `200` to `0`. Leave
`templates/configmap.yaml` unchanged because it already quotes
`.Values.config.backpressureMaxConcurrentRequests`.

Cover the contract in `tests/unit/test_helm_replica_artifacts.py` with a
real `helm template` assertion against the rendered ConfigMap, not a
values-file string check. Use `37` as the explicit override so the test
cannot pass by echoing a leftover `200`.

Keep the optional positive override. Operators who want a global cap can
still set the Helm value; the default install must not.

## Risks / Trade-offs

- Un-overridden Helm upgrades lose the previous global cap of `200`.
  Mitigation: that cap contradicted split-by-class admission and the
  application default. Operators who need a global limit set the value
  explicitly.
- A values-only test could miss ConfigMap wiring drift. Mitigation: parse
  the rendered ConfigMap `data` field from `helm template`.
