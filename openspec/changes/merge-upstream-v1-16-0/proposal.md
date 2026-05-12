## Why

Local `main` is now aligned to `origin/main` at `v1.15.2`, while `upstream/main` has advanced through `v1.16.0` and additional proxy, dashboard, OpenSpec, CI, and runtime fixes. The branches have diverged, and a trial merge predicts conflicts in proxy runtime code, OpenSpec specs, frontend schemas, packaging/version files, and lockfiles, so the upstream update needs an explicit integration plan rather than a direct merge.

This change captures the merge plan as an OpenSpec work item so upstream behavior can be adopted without losing fork-specific behavior such as the `codex-lb-cinamon` package identity, release automation, and local continuity fallback protections.

## What Changes

- Define the merge scope from current `main` to `upstream/main`, using merge base `ca05f877` and upstream head `e5efbef` as the inspected comparison points.
- Adopt upstream changes for files upload protocol, OpenAI-compatible images API, strict Responses validation, streaming stability, quota recovery, dashboard session lifetime, account quota display, request-log filtering, background database pool sizing, Helm/Kubernetes support, and type/test cleanup.
- Preserve fork-specific behavior from `main`, especially package and CLI names, repository metadata, release-please configuration, local Platform fallback behavior, and `protect-codex-continuity-from-platform-fallback`.
- Align the fork's version surfaces to upstream `1.16.0` after the merge while keeping the fork package identity as `codex-lb-cinamon`.
- Reconcile predicted conflicts in `.github/release-please-manifest.json`, `CHANGELOG.md`, `README.md`, `app/__init__.py`, proxy request/runtime modules, request-log repository, Helm README, frontend package/account/dashboard files, `responses-api-compat` spec, `pyproject.toml`, targeted tests, and `uv.lock`.
- Establish validation gates for OpenSpec, Python lint/typecheck/tests, Alembic graph and upgrade behavior, Helm rendering, frontend checks, and a final semantic review of fork-specific behavior.

## Capabilities

### New Capabilities

- `files-upload-protocol`: Backend file upload and file-id routing behavior introduced by upstream.
- `images-api-compat`: OpenAI-compatible images API behavior introduced by upstream.

### Modified Capabilities

- `admin-auth`: Dashboard session lifetime and auth-session behavior from upstream must merge without weakening existing auth boundaries.
- `api-keys`: API-key usage limits, reset windows, request-log filtering, and enforced service-tier behavior must remain coherent after the merge.
- `database-backends`: Background database pool sizing and startup behavior must match upstream without regressing local database compatibility.
- `database-migrations`: Alembic graph changes, legacy remaps, and request-log/dashboard-session migrations must converge to a single safe head.
- `deployment-installation`: Helm/Kubernetes support and CI render checks must be reconciled with fork release and package metadata.
- `frontend-architecture`: Dashboard, settings, accounts, quota display, and request-log schemas must match the merged backend contract.
- `proxy-runtime-observability`: Drain status, SSE keepalives, transient retry metrics, and request-log fields must survive conflict resolution.
- `responses-api-compat`: Responses validation, file/image inputs, streaming deltas, unsupported parameter stripping, built-in tools, and continuity protections must be merged together.
- `runtime-portability`: Portable debug dump paths and fork-specific package/CLI identity must be resolved intentionally.
- `sticky-session-operations`: Continuity, sticky routing, file-id affinity, and owner handoff behavior must remain deterministic.
- `usage-refresh-policy`: Quota recovery and primary-usage budget gates must align with upstream while preserving local fallback semantics.

## Impact

- Merge target: current `main` at `v1.15.2`.
- Upstream source: `upstream/main` at `e5efbef`, including tag `v1.16.0`.
- Post-merge version target: `1.16.0` across `pyproject.toml`, `app/__init__.py`, `frontend/package.json`, `uv.lock`, and release-please metadata.
- Diff size observed: 376 files, about 15k insertions and 23k deletions.
- High-risk areas: `app/modules/proxy/api.py`, `app/modules/proxy/service.py`, `app/core/openai/requests.py`, `app/modules/proxy/load_balancer.py`, `app/db/alembic/**`, `frontend/src/features/**`, OpenSpec main specs, `pyproject.toml`, `frontend/package.json`, and `uv.lock`.
- Validation must use relative paths and local/Podman CI-equivalent commands; no container image build is required unless the implementation changes image packaging behavior.
