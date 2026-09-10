## 1. Regression Coverage

- [x] 1.1 Add a real Helm-render assertion in
      `tests/unit/test_helm_replica_artifacts.py` that the default ConfigMap
      `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS` equals `"0"` and that
      `--set config.backpressureMaxConcurrentRequests=37` renders `"37"`.
- [x] 1.2 Run that focused test before changing values and capture the RED
      failure showing the actual default `"200"`.

## 2. Chart Default

- [x] 2.1 Change only `deploy/helm/codex-lb/values.yaml`
      `config.backpressureMaxConcurrentRequests` from `200` to `0`.

## 3. Verification

- [x] 3.1 Re-run the focused Helm-render test and confirm GREEN.
- [x] 3.2 Run settings backpressure tests, Helm lint/template checks,
      formatting/diff checks, and scoped strict OpenSpec validation.
- [x] 3.3 Render the chart with literal `helm template` for the default and
      the `37` override, parse the ConfigMap values, and confirm exact `0`
      and `37`.
