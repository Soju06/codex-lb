PYTEST_ARGS := -q -ra -o faulthandler_timeout=300 -o faulthandler_exit_on_timeout=true --timeout=180 --timeout-method=thread --durations=20
POSTGRES_TEST_DATABASE_URL ?= postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb
INTEGRATION_CORE_SHARD_COUNT := 3
POSTGRES_PYTEST_TARGETS := \
	tests/integration/test_migrations.py::test_postgresql_migration_contract_policy_and_drift_match \
	tests/integration/test_migrations.py::test_postgresql_upgrade_head_from_empty_database \
	tests/integration/test_migrations.py::test_postgresql_startup_migration_auto_remap_legacy_head \
	tests/integration/test_migration_serialization.py::test_concurrent_upgrades_on_fresh_postgresql_database_apply_head_exactly_once \
	tests/integration/test_migration_serialization.py::test_postgresql_run_upgrade_times_out_when_advisory_lock_is_held \
	tests/integration/test_usage_repository.py::test_latest_by_account_primary_query_plan_uses_normalized_window_index_postgresql \
	tests/integration/test_automations_history_queries.py \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_enabled_serializes_concurrent_same_email \
	tests/integration/test_sticky_sessions_api.py::test_durable_bridge_owned_alias_registration_is_epoch_fenced \
	tests/integration/test_proxy_api_extended.py::test_proxy_stream_usage_limit_returns_http_error \
	tests/integration/test_api_keys_api.py::test_rate_limit_header_failure_releases_reservation_once \
	tests/integration/test_codex_usage_api.py::test_codex_usage_aggregates_windows \
	tests/integration/test_proxy_compact.py::test_proxy_compact_headers_include_monthly_only_credits \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_disabled_uses_identity_lock_on_postgresql \
	tests/integration/test_db_session_timezone.py \
	tests/integration/test_db_commit_durability.py \
	tests/test_request_logs_options_api.py \
	tests/integration/test_account_usage_rollup.py \
	tests/integration/test_account_deletion_background.py \
	tests/integration/test_request_usage_time_rollup.py \
	tests/integration/test_request_usage_rollup_parity.py \
	tests/integration/test_migrations.py::test_request_usage_time_rollups_migration_upgrade_and_downgrade \
	tests/integration/test_migrations.py::test_conversation_presence_rollup_migration_upgrade_and_downgrade \
	tests/integration/test_data_retention.py \
	tests/integration/test_plan_downgrade_observation_store.py \
	tests/integration/test_accounts_api_probe.py::test_force_probe_confirms_paid_to_free_plan_downgrade \
	tests/integration/test_accounts_api_probe.py::test_force_probe_keeps_paid_plan_for_unrecognized_payload_plan \
	tests/integration/test_accounts_api_probe.py::test_pending_downgrade_evidence_is_persisted_for_all_replicas \
	tests/integration/test_accounts_api_probe.py::test_reimport_clears_pending_downgrade_evidence \
	tests/integration/test_repositories.py::test_replace_reauthorized_discards_pending_downgrade_evidence \
	tests/integration/test_repositories.py::test_upsert_account_slot_discards_pending_downgrade_evidence_on_reimport \
	tests/integration/test_migrations.py::test_account_plan_downgrade_observations_migration_upgrade_and_downgrade \
	tests/integration/test_migrations.py::test_account_pending_deletion_migration_upgrade_and_downgrade \
	tests/integration/test_usage_repository.py::test_bulk_history_since_primary_query_plan_is_index_only_postgresql \
	tests/integration/test_usage_repository.py::test_bulk_history_since_cutoff_query_plan_is_index_only_postgresql \
	tests/integration/test_usage_repository.py::test_bulk_history_since_secondary_query_plan_is_index_only_postgresql \
	tests/integration/test_usage_repository.py::test_bulk_history_since_covered_read_matches_non_covered_read_postgresql \
	tests/integration/test_usage_repository.py::test_bulk_history_since_per_account_row_cap_keeps_newest_rows \
	tests/integration/test_usage_repository.py::test_bulk_history_since_row_cap_respects_per_account_cutoffs_postgresql \
	tests/integration/test_usage_repository.py::test_bulk_history_since_row_cap_exempts_uncapped_recent_floor_postgresql \
	tests/integration/test_usage_repository.py::test_bulk_history_since_capped_query_plan_is_index_only_postgresql \
	tests/integration/test_usage_repository.py::test_bulk_history_since_capped_floor_query_plan_is_index_only_postgresql \
	tests/integration/test_migrations.py::test_usage_history_bulk_covering_indexes_migration_upgrade_and_downgrade \
	tests/integration/test_migrations.py::test_usage_history_covering_index_migration_repairs_invalid_leftover_postgresql \
	tests/integration/test_migrations.py::test_usage_history_autovacuum_tuning_migration_sets_and_resets_reloptions_postgresql
SHELL := /bin/bash

.PHONY: help
help:
	@printf '%s\n' \
	  'Common targets:' \
	  '  make lint                    ruff check + format check + architecture checks' \
	  '  make architecture-check      proxy architecture fitness ratchets' \
	  '  make typecheck               ty check' \
	  '  make rust-check              fmt + clippy + tests + release build' \
	  '  make rust-audit              cargo-deny dependency policy' \
	  '  make frontend-test           vitest coverage, same as CI' \
	  '  make test-dashboard-browser-smoke  built dashboard against the real local API' \
	  '  make test-unit               unit pytest slice, same as CI' \
	  '  make test-integration-core   integration-core pytest slice' \
	  '  make package                 build and verify sdist/wheel' \
	  '  make ci-fast                 lint/type/frontend/unit/package/rust-check' \
	  '  make ci                      full local CI gate'

.PHONY: frontend-install frontend-lint frontend-typecheck frontend-test frontend-test-fast frontend-build \
	frontend-playwright-chromium test-dashboard-browser-smoke
frontend-install:
	cd frontend && bun install --frozen-lockfile

frontend-lint: frontend-install
	cd frontend && bun run lint

frontend-typecheck: frontend-install
	cd frontend && bun run typecheck

frontend-test: frontend-install
	cd frontend && bun run test:coverage

frontend-test-fast: frontend-install
	cd frontend && bun run test

frontend-build: frontend-install
	cd frontend && bun run build

frontend-playwright-chromium: frontend-install
	cd frontend && bun run playwright install chromium

test-dashboard-browser-smoke: frontend-build frontend-playwright-chromium
	uv sync --dev --frozen
	uv run python scripts/run_dashboard_browser_smoke.py --frontend-built

.PHONY: lint typecheck architecture-check rust-fmt rust-lint rust-test rust-build rust-check rust-audit
lint: architecture-check
	uv run ruff check .
	uv run ruff format --check .

architecture-check:
	uv run python scripts/check_proxy_architecture.py
	uv run python scripts/check_cancellation_safety.py

typecheck:
	uv sync --dev --frozen
	uv run ty check

rust-fmt:
	cargo fmt --all -- --check

rust-lint:
	cargo clippy --workspace --all-targets --all-features --locked -- -D warnings

rust-test:
	cargo test --workspace --all-targets --locked

rust-build:
	cargo build --release --locked --package codex-lb-egress-worker --bin codex-lb-native-egress

rust-check: rust-fmt rust-lint rust-test rust-build

rust-audit:
	cargo deny --all-features check

.PHONY: test-unit test-integration-core test-integration-core-shard \
	test-integration-core-1 test-integration-core-2 test-integration-core-3 \
	test-integration-bridge test-e2e test-postgres
test-unit: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/unit tests/test_request_logs_options_api.py

test-integration-core: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/integration \
	  --ignore=tests/integration/test_http_responses_bridge.py \
	  --ignore=tests/integration/test_proxy_websocket_responses.py

# CI splits integration-core into deterministic shards (test-count-weighted
# greedy assignment; see .github/scripts/pytest_shards.py). The --verify call
# guards that the shards always partition the full selection exactly.
test-integration-core-shard: frontend-build
	uv sync --dev --frozen
	uv run python .github/scripts/pytest_shards.py --shard-count $(INTEGRATION_CORE_SHARD_COUNT) --verify
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) \
	  $$(uv run python .github/scripts/pytest_shards.py --shard-count $(INTEGRATION_CORE_SHARD_COUNT) --shard $(SHARD))

test-integration-core-1:
	$(MAKE) test-integration-core-shard SHARD=1

test-integration-core-2:
	$(MAKE) test-integration-core-shard SHARD=2

test-integration-core-3:
	$(MAKE) test-integration-core-shard SHARD=3

test-integration-bridge: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) -vv \
	  tests/integration/test_http_responses_bridge.py \
	  tests/integration/test_proxy_websocket_responses.py

test-e2e: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/e2e

test-postgres:
	uv sync --dev --frozen
	CODEX_LB_TEST_DATABASE_URL="$${CODEX_LB_TEST_DATABASE_URL:-$(POSTGRES_TEST_DATABASE_URL)}" \
	  PYTHONFAULTHANDLER=1 \
	  uv run pytest $(PYTEST_ARGS) $(POSTGRES_PYTEST_TARGETS)

.PHONY: migration-check migration-check-postgres
migration-check:
	uv sync --dev --frozen
	TMP_DB="$$(mktemp -u /tmp/codex-lb-ci-migrate-XXXXXX.db)"; \
	DB_URL="sqlite+aiosqlite:///$${TMP_DB}"; \
	trap 'rm -f "$${TMP_DB}"' EXIT; \
	uv run codex-lb-db --db-url "$${DB_URL}" upgrade head; \
	uv run codex-lb-db --db-url "$${DB_URL}" check

migration-check-postgres:
	uv sync --dev --frozen
	uv run codex-lb-db --db-url "$(POSTGRES_TEST_DATABASE_URL)" upgrade head
	uv run codex-lb-db --db-url "$(POSTGRES_TEST_DATABASE_URL)" check

.PHONY: package
package: frontend-build
	uv sync --frozen --no-dev
	uv run python -c "import app; import app.main; print('import ok')"
	rm -rf build dist *.egg-info
	uvx --from build==1.3.0 python -m build
	uv run python scripts/verify-wheel-assets.py

.PHONY: docker
docker:
	docker build -t codex-lb:ci .
	trivy image --format table --exit-code 1 --severity CRITICAL --ignore-unfixed codex-lb:ci

.PHONY: helm-deps helm-lint helm-template helm-kubeconform
helm-deps:
	helm dependency build deploy/helm/codex-lb/

helm-lint: helm-deps
	helm lint --strict deploy/helm/codex-lb/ --set postgresql.auth.password=test-password
	helm lint --strict deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-dev.yaml --set postgresql.auth.password=test-password
	helm lint --strict deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-bundled.yaml --set postgresql.auth.password=test-password
	helm lint --strict deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-external-db.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test
	helm lint --strict deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-external-secrets.yaml --set externalSecrets.secretStoreRef.name=test-store
	helm lint --strict deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-staging.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test
	helm lint --strict deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-prod.yaml --set externalSecrets.secretStoreRef.name=test-store

helm-template:
	helm template codex-lb deploy/helm/codex-lb/ --set postgresql.auth.password=test-password > /dev/null
	helm template codex-lb deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-dev.yaml --set postgresql.auth.password=test-password > /dev/null
	helm template codex-lb deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-bundled.yaml --set postgresql.auth.password=test-password > /dev/null
	helm template codex-lb deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-external-db.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test > /dev/null
	helm template codex-lb deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-external-secrets.yaml --set externalSecrets.secretStoreRef.name=test-store > /dev/null
	helm template codex-lb deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-staging.yaml --set externalDatabase.url=postgresql+asyncpg://test:test@localhost/test > /dev/null
	helm template codex-lb deploy/helm/codex-lb/ -f deploy/helm/codex-lb/values-prod.yaml --set externalSecrets.secretStoreRef.name=test-store > /dev/null

helm-kubeconform:
	set -e -o pipefail; \
	for version in 1.32.0 1.35.0; do \
	  helm template codex-lb deploy/helm/codex-lb/ \
	    -f deploy/helm/codex-lb/values-prod.yaml \
	    --set externalSecrets.secretStoreRef.name=test \
	    --set externalSecrets.secretStoreRef.kind=SecretStore \
	    --set gatewayApi.enabled=true \
	    --set "gatewayApi.parentRefs[0].name=test-gw" \
	    --set "gatewayApi.hostnames[0]=test.example.com" \
	    | kubeconform \
	      -strict \
	      -kubernetes-version "$${version}" \
	      -schema-location default \
	      -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
	      -summary; \
	done

.PHONY: helm-check helm-smoke-kind
helm-check: helm-lint helm-template helm-kubeconform

helm-smoke-kind:
	kind create cluster --name codex-lb-smoke --image kindest/node:v1.35.0 --wait 120s
	docker build -t ghcr.io/soju06/codex-lb:ci .
	kind load docker-image ghcr.io/soju06/codex-lb:ci --name codex-lb-smoke
	KUBE_CONTEXT=kind-codex-lb-smoke IMAGE_REGISTRY=ghcr.io IMAGE_REPOSITORY=soju06/codex-lb IMAGE_TAG=ci ./scripts/helm-kind-smoke.sh bundled
	KUBE_CONTEXT=kind-codex-lb-smoke IMAGE_REGISTRY=ghcr.io IMAGE_REPOSITORY=soju06/codex-lb IMAGE_TAG=ci ./scripts/helm-kind-smoke.sh external-db

.PHONY: ci-fast ci
ci-fast: lint typecheck rust-check frontend-test test-unit package

ci: frontend-lint frontend-typecheck frontend-test frontend-build lint typecheck rust-check rust-audit \
	test-unit test-integration-core test-integration-bridge test-e2e test-postgres \
	migration-check migration-check-postgres package docker helm-check helm-smoke-kind
