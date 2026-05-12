## Context

Current `main` is aligned to `origin/main` at `v1.15.2`. `upstream/main` is at `e5efbef` and includes `v1.16.0` plus later fixes. The merge base is `ca05f877`, and the inspected diff is large: 376 files, about 15k insertions and 23k deletions.

The merge is not a fast-forward. A dry merge calculation reports conflicts in:

- `.github/release-please-manifest.json`
- `CHANGELOG.md`
- `README.md`
- `app/__init__.py`
- `app/core/openai/requests.py`
- `app/modules/proxy/api.py`
- `app/modules/proxy/service.py`
- `app/modules/request_logs/repository.py`
- `deploy/helm/codex-lb/README.md`
- `frontend/package.json`
- `frontend/src/features/accounts/components/account-list-item.tsx`
- `frontend/src/features/accounts/components/accounts-page.tsx`
- `frontend/src/features/dashboard/schemas.test.ts`
- `openspec/specs/responses-api-compat/spec.md`
- `pyproject.toml`
- `tests/integration/test_v1_models.py`
- `tests/unit/test_load_balancer.py`
- `tests/unit/test_proxy_utils.py`
- `tests/unit/test_select_with_stickiness.py`
- `uv.lock`

## Goals

- Adopt upstream behavior through `upstream/main` without losing current `main` behavior.
- Keep the fork-specific package, CLI, repository, and release automation surfaces intentional.
- Match upstream's release version at `1.16.0` after the merge while preserving fork-specific names and repository metadata.
- Keep OpenSpec as the source of truth for behavior, with implementation and tests reconciled to it.
- Make conflict resolution reviewable by subsystem rather than as one broad mechanical merge.

## Non-Goals

- Do not rename the fork package back to upstream `codex-lb`.
- Do not directly hand-edit changelog content beyond conflict resolution required to preserve release history.
- Do not archive unrelated active OpenSpec changes as part of the merge.
- Do not require a container image build as the default validation gate.

## Merge Strategy

1. Start from current `main`, not a feature branch baseline, because the requested integration target is `main`.
2. Create an integration commit or staged merge from `upstream/main` using `--no-ff --no-commit` during implementation so conflicts can be resolved before committing.
3. Resolve metadata conflicts first:
   - Keep `codex-lb-cinamon` package name, fork URLs, fork CLI entry points, and release-please configuration.
   - Set the merged fork version to `1.16.0` across Python, frontend, lockfile, and release-please surfaces.
   - Preserve upstream dependency constraints when they do not revert fork identity.
4. Resolve OpenSpec conflicts before code conflicts where possible:
   - Keep local requirements for Platform fallback, continuity protection, API-key enforced tiers, and CLI lifecycle behavior unless explicitly superseded.
   - Add upstream requirements for files protocol, images API, session lifetime, quota recovery, strict validation, stream keepalive, and Helm policy.
5. Resolve backend runtime conflicts by subsystem:
   - Proxy/Responses normalization and routing.
   - Sticky-session and durable bridge continuity.
   - API-key and request-log repository behavior.
   - Alembic graph and database session/pool behavior.
6. Resolve frontend conflicts after backend schemas are fixed so generated or hand-maintained TypeScript schemas match the merged API.
7. Rebuild lockfiles only after `pyproject.toml` and `frontend/package.json` are intentionally resolved.

## Risk Areas

- `app/modules/proxy/service.py` and `app/modules/proxy/api.py` were substantially rewritten upstream and also contain local continuity and Platform fallback behavior.
- Upstream removes OpenAI Platform identity modules that local fallback behavior may still require.
- Upstream package metadata conflicts with the fork's package and release identity.
- Version conflicts must not leave mixed `1.15.2` / `1.16.0` surfaces after resolution.
- Alembic remap changes can silently skip local bridge or Platform fallback migrations if resolved incorrectly.
- Frontend mocks and schemas can pass unit tests while drifting from backend payloads unless integration tests are included.
- `openspec validate --specs` alone cannot prove semantic merge safety; targeted runtime tests are required.

## Validation Plan

- OpenSpec: `uv run openspec validate --specs` and, if CI parity is needed, the pinned OpenSpec CLI form used by workflows.
- Python style/type: `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`.
- Python tests:
  - migration tests for Alembic graph and legacy remap behavior,
  - proxy tests for Responses, WebSocket, files/images, strict schema, sticky routing, continuity fallback, stream retry, and model fetch timeout,
  - API-key/request-log tests for reset windows and filtering,
  - auth/settings tests for dashboard session lifetime.
- Database: use Podman-backed PostgreSQL for migration/repository tests if host services are not ready.
- Helm: lint and template the reference chart and External Secrets success/failure cases.
- Frontend: run lint/typecheck/build/test or the existing containerized Node/Bun-equivalent checks when host tooling is insufficient.
- Hygiene: `git diff --check`, `git diff --cached --check`, and a final reviewed inventory of local-only behavior that survived the merge.

## Rollback Plan

Keep the merge as a single integration commit after conflicts and validation pass. If a validation gate fails late, reset the uncommitted merge state before commit and reapply the accepted conflict resolutions by subsystem. If the merge is already committed but not pushed, revert the integration commit rather than rewriting unrelated local history.
