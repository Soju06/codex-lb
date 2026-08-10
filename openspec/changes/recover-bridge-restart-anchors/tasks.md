- [x] Add a per-process durable bridge owner epoch and additive migration.
- [x] Retire previous-process same-instance durable bridge rows during startup.
- [x] Return non-retryable fresh-turn guidance for proven-dead durable owners.
- [x] Keep genuine transient upstream silence on existing retryable idle semantics.
- [x] Poison repeated zero-event idle anchors using retry-circuit state.
- [x] Add focused regressions for restart retirement, dead-owner semantics, and anchor poisoning.
- [x] Run strict OpenSpec validation and the full test suite; full suite has one unrelated
  `test_quota_planner_warm_now_keeps_bootstrap_for_metadata_less_primary_rows`
  failure that reproduces on clean `origin/main`.
- [x] Commit and push `bridge-restart-recovery`.

- [x] 3.9 Post-deploy regression: normalize timestamptz lease expiry vs naive clock in `lease_is_active` (production TypeError on the anchored-lookup path in v1.23.0-beta.5).
