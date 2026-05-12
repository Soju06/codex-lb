## 1. Baseline And Safety Setup

- [x] 1.1 Align local `main` to latest `origin/main` before planning.
- [x] 1.2 Fetch `upstream/main` and identify merge base, upstream head, diff size, and predicted conflict files.
- [x] 1.3 Record fork-specific behavior that must survive: package name, CLI entry points, release-please configuration, Platform fallback boundaries, continuity protection, and current OpenSpec deltas.
- [x] 1.4 Before implementation, confirm the working tree contains only intended OpenSpec plan files plus known local-only untracked settings.

## 2. OpenSpec Reconciliation

- [x] 2.1 Resolve `openspec/specs/responses-api-compat/spec.md` by merging upstream Responses fixes with local continuity fallback requirements.
- [x] 2.2 Add or reconcile specs for upstream files upload and images API behavior.
- [x] 2.3 Reconcile database, deployment, frontend, auth, API-key, sticky-session, runtime-portability, and usage-refresh requirements.
- [x] 2.4 Run OpenSpec validation and fix any strict purpose, duplicate requirement, or archive/main drift issues.

## 3. Metadata, Release, And Lockfiles

- [x] 3.1 Resolve `pyproject.toml`, `uv.lock`, `app/__init__.py`, `.github/release-please-manifest.json`, and release workflow conflicts while preserving fork package identity and setting the fork version to `1.16.0`.
- [x] 3.2 Resolve `frontend/package.json` and frontend lockfile conflicts so frontend version surfaces also read `1.16.0`.
- [x] 3.3 Treat `CHANGELOG.md`, `README.md`, and Helm README conflicts as merge-history reconciliation only; do not add new behavior docs outside OpenSpec.

## 4. Backend Runtime Merge

- [x] 4.1 Resolve proxy API/service conflicts around Responses, files/images, stream retry, SSE keepalive, strict validation, unsupported parameters, and oversized history handling.
- [x] 4.2 Preserve local Platform fallback and continuity-protection behavior in public and owner-forwarded paths.
- [x] 4.3 Reconcile load-balancer, sticky-session, file-id affinity, quota recovery, and primary budget-safe gate changes.
- [x] 4.4 Reconcile request-log repository and API-key reset/filtering behavior.
- [x] 4.5 Reconcile auth/settings/session lifetime behavior.
- [x] 4.6 Reconcile Alembic revisions, revision remaps, and database pool/session behavior to one safe head.

## 5. Frontend Merge

- [x] 5.1 Resolve account list, accounts page, dashboard schemas, settings session UI, quota display, and request-log filtering conflicts.
- [x] 5.2 Update frontend schemas, mocks, and tests so they match the merged backend payloads.
- [x] 5.3 Verify local-only fields and fork-specific labels are not dropped during upstream UI adoption.

## 6. Validation Gates

- [x] 6.1 Run `uv run openspec validate --specs`.
- [x] 6.2 Run `uv run ruff check`, `uv run ruff format --check`, and `uv run ty check`.
- [x] 6.3 Run targeted Python tests for migrations, proxy Responses/WebSocket/files/images, load balancer, sticky sessions, API keys, request logs, auth, settings, health, and usage refresh.
- [x] 6.4 Run Podman-backed PostgreSQL migration/repository tests if host DB services are not ready.
- [x] 6.5 Run Helm lint/template checks for the reference chart and External Secrets success/failure cases.
- [x] 6.6 Run frontend lint/typecheck/build/test using host tooling or Podman if host tooling is insufficient.
- [x] 6.7 Run `git diff --check` and review the final diff for lost local-only behavior.

## 7. Delivery

- [x] 7.1 Commit the verified merge as a focused upstream integration commit.
- [x] 7.2 Push only after all relevant local CI-equivalent checks pass or are explicitly documented as blocked.
- [x] 7.3 Prepare the PR summary with actual checks run, unresolved risks, and confirmation that the merged fork version is `1.16.0`.
