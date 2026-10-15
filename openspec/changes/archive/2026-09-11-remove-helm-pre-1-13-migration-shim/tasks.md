# Tasks

## 1. Chart

- [x] 1.1 Delete `templates/legacy-deployment-prepare-hook.yaml` and `templates/legacy-deployment-cleanup-hook.yaml`.
- [x] 1.2 `templates/service.yaml`: replace the `lookup`/`serviceSelectorMode` branch with an unconditional `codex-lb.workloadSelectorLabels` selector.
- [x] 1.3 `templates/_helpers.tpl`: delete `codex-lb.legacySelectorLabels`; restate why the workload lane label survives (StatefulSet selectors are immutable).
- [x] 1.4 `values.yaml`: delete `migration.serviceSelectorMode` and its `@param`/`DEPRECATED` comments.

## 2. Documentation

- [x] 2.1 `README.md` `Upgrading`: state the removal as shipped, give the `1.24.x` -> this-release path with verification commands, and drop the planned-removal notice that the code now contradicts.
- [x] 2.2 `README.md` `Upgrade Contract`: point the workload-name bullet at the new two-step path.

## 3. Tests

- [x] 3.1 Replace the shim-renders tests with `test_upgrade_renders_no_legacy_deployment_migration_hooks`, `test_public_service_always_selects_the_statefulset_workload_lane` and `test_removed_service_selector_mode_value_no_longer_changes_the_selector`.
- [x] 3.2 Keep hook-Job image-pull-secret coverage on the surviving migration Job.

## 4. Verification

- [x] 4.1 `helm lint deploy/helm/codex-lb`; `helm template` with defaults, `--is-upgrade`, and `--set migration.serviceSelectorMode=legacy`.
- [x] 4.2 `uv run pytest tests/unit/test_helm_external_secrets.py tests/unit/test_helm_replica_artifacts.py tests/unit/test_helm_monitoring_artifacts.py tests/unit/test_helm_shutdown_contract.py -q`.
- [x] 4.3 `make lint`, `python3 .github/scripts/check_simplicity_budgets.py`.
- [x] 4.4 `openspec validate remove-helm-pre-1-13-migration-shim --strict` and `openspec validate --specs --strict`.
