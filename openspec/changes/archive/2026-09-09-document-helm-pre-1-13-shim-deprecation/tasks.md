# Tasks

## 1. Documentation

- [x] 1.1 `deploy/helm/codex-lb/README.md`: `## Upgrading` with the pre-1.13 shim (hooks, `migration.serviceSelectorMode`, fullname-keyed verification, maintenance-window caveat, no-op-on-newer-releases note, dry-run limits), the deprecation statement, and the `1.24 -> 1.25` settings note (removed variables, deprecated aliases, which of them log the shadowing WARN).
- [x] 1.2 `deploy/helm/codex-lb/values.yaml`: `DEPRECATED` comment on `migration.serviceSelectorMode`; alias notes on `config.upstreamConnectTimeout` and `config.circuitBreakerEnabled`.

## 2. Verification

- [x] 2.1 `helm lint deploy/helm/codex-lb`, `make helm-lint helm-template`, `uv run pytest tests/unit/test_helm_replica_artifacts.py`, `python3 .github/scripts/check_simplicity_budgets.py`.
- [x] 2.2 `openspec validate document-helm-pre-1-13-shim-deprecation --strict` and `openspec validate --specs --strict`.
