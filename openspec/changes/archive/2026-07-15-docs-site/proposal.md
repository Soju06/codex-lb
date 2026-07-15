## Why

The README has grown to 650+ lines and `.env.example` to 115 lines with ~45 active values — five of which contradict the code defaults, so anyone copying the sample silently misconfigures their install (e.g. stream idle timeout 300s vs the 7200s default). The project's founding promise is 1-click setup; the entry-point documents now bury it. Detailed material (client configs, auth modes, Postgres upgrade runbooks, Kubernetes ops) needs a real home so the README can shrink back to a quickstart without losing content.

## What Changes

- Add a mkdocs-material documentation site (`mkdocs.yml`, pages under `docs/`, existing `docs/screenshots/` reused) published to https://soju06.github.io/codex-lb/ via a new `.github/workflows/docs.yml` (strict build on PRs, GitHub Pages deploy on pushes to `main`).
- Add a `docs` uv dependency group (`mkdocs-material`) and regenerate `uv.lock`; ignore the `site/` build output.
- Diet README.md to a ~120-hand-written-line quickstart (hero, features, Quick Start, one Codex CLI config block plus a client table, configuration pointer, data table, docs links, development). All moved content lands on docs pages; the all-contributors block stays.
- Add a banner to README.zh-CN.md pointing at the English docs site as canonical.
- Diet `.env.example` to a fully commented ~40-line sample with no values that contradict code defaults (drops the five drifted values), keeping the commented leader-election escape hatch that `tests/unit/test_helm_replica_artifacts.py` pins.
- Append a user-docs sentence to the `context:` block in `openspec/config.yaml`.

## Capabilities

### New Capabilities

- `user-documentation`: the published docs site, its build/deploy gates, README scope, and `.env.example` hygiene.

### Modified Capabilities

None.

## Impact

- Docs/site: `mkdocs.yml`, `docs/*.md`, `docs/deployment/*.md`, `.github/workflows/docs.yml`
- Entry points: `README.md`, `README.zh-CN.md`, `.env.example`
- Tooling: `pyproject.toml` (`docs` dependency group), `uv.lock`, `.gitignore`
- Specs: `openspec/specs/user-documentation/spec.md` (new), `openspec/config.yaml` context note
- Out of band: a repo admin must enable GitHub Pages (`build_type=workflow`) before the first `main` deploy
