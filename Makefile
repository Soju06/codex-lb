PYTEST_ARGS := -q -ra -o faulthandler_timeout=300 -o faulthandler_exit_on_timeout=true --timeout=180 --timeout-method=thread --durations=20
POSTGRES_TEST_DATABASE_URL ?= postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb
POSTGRES_PYTEST_TARGETS := \
	tests/integration/test_migrations.py::test_postgresql_migration_contract_policy_and_drift_match \
	tests/integration/test_migrations.py::test_postgresql_upgrade_head_from_empty_database \
	tests/integration/test_migrations.py::test_postgresql_startup_migration_auto_remap_legacy_head \
	tests/integration/test_usage_repository.py::test_latest_by_account_primary_query_plan_uses_normalized_window_index_postgresql \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_enabled_serializes_concurrent_same_email \
	tests/integration/test_proxy_api_extended.py::test_proxy_stream_usage_limit_returns_http_error \
	tests/integration/test_repositories.py::test_accounts_upsert_with_merge_disabled_uses_identity_lock_on_postgresql
SHELL := /bin/bash

.PHONY: help
help:
	@printf '%s\n' \
	  'Common targets:' \
	  '  make lint                    ruff check + format check + architecture checks' \
	  '  make architecture-check      proxy architecture fitness ratchets' \
	  '  make typecheck               ty check' \
	  '  make frontend-test           vitest coverage, same as CI' \
	  '  make test-unit               unit pytest slice, same as CI' \
	  '  make test-integration-core   integration-core pytest slice' \
	  '  make package                 build and verify sdist/wheel' \
	  '  make ci-fast                 lint/type/frontend/unit/package' \
	  '  make ci                      full local CI gate'

.PHONY: frontend-install frontend-lint frontend-typecheck frontend-test frontend-build
frontend-install:
	cd frontend && bun install --frozen-lockfile

frontend-lint: frontend-install
	cd frontend && bun run lint

frontend-typecheck: frontend-install
	cd frontend && bun run typecheck

frontend-test: frontend-install
	cd frontend && bun run test:coverage

frontend-build: frontend-install
	cd frontend && bun run build

.PHONY: lint typecheck architecture-check
lint: architecture-check
	uvx ruff check .
	uvx ruff format --check .

architecture-check:
	python scripts/check_proxy_architecture.py

typecheck:
	uv sync --dev --frozen
	uv run ty check

.PHONY: test-unit test-integration-core test-integration-bridge test-e2e test-postgres
test-unit: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/unit tests/test_request_logs_options_api.py

test-integration-core: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/integration \
	  --ignore=tests/integration/test_http_responses_bridge.py \
	  --ignore=tests/integration/test_proxy_websocket_responses.py

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

.PHONY: migration-check migration-check-postgres i18n-check
migration-check:
	uv sync --dev --frozen
	TMP_DB="$$(mktemp -u /tmp/codex-lb-ci-migrate-XXXXXX.db)"; \
	DB_URL="sqlite+aiosqlite:///$${TMP_DB}"; \
	trap 'rm -f "$${TMP_DB}"' EXIT; \
	uv run codex-lb-db --db-url "$${DB_URL}" upgrade head; \
	uv run codex-lb-db --db-url "$${DB_URL}" check

i18n-check:
	./scripts/check_i18n_parity.sh

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
	python scripts/verify-wheel-assets.py

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
ci-fast: lint typecheck frontend-test test-unit package

ci: frontend-lint frontend-typecheck frontend-test frontend-build lint typecheck \
	test-unit test-integration-core test-integration-bridge test-e2e test-postgres \
	migration-check migration-check-postgres package docker helm-check helm-smoke-kind
