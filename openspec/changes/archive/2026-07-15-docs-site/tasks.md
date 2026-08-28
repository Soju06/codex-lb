## 1. Docs site scaffold

- [x] 1.1 Add `mkdocs.yml` (material theme, light/dark palettes, strict validation block, edit links, nav covering all pages).
- [x] 1.2 Add the `docs` uv dependency group (`mkdocs-material>=9.6`), run `uv lock`, and add `site/` to `.gitignore`.
- [x] 1.3 Add `.github/workflows/docs.yml`: strict mkdocs build on PRs and pushes; upload + deploy to GitHub Pages only on non-PR events; SHA-pinned actions resolved from upstream tags; least-privilege permissions.

## 2. Content migration

- [x] 2.1 Create docs pages (index, getting-started, client-setup, configuration, authentication, api-keys, routing, database, deployment/{docker,kubernetes,remote}, troubleshooting) absorbing all README sections that leave the diet, including the Postgres 16→18 upgrade runbook verbatim.
- [x] 2.2 Rewrite repo-relative links (openspec contexts, Helm README, .env.example, docker-compose) to absolute GitHub URLs on docs pages; fix the invalid OpenClaw JSONC (missing commas); use the previously unreferenced screenshots (apis-assigned-accounts.jpg, codex-session-retag-*.png).
- [x] 2.3 Add a spec footer link to the governing openspec capability on every page that documents spec-governed behavior.

## 3. Entry-point diet

- [x] 3.1 Rewrite README.md to a slim quickstart (~120 hand-written lines) with one prominent documentation link line; keep the all-contributors block and markers intact.
- [x] 3.2 Add the canonical-English-docs banner to README.zh-CN.md without restructuring the rest.
- [x] 3.3 Rewrite `.env.example`: fully commented, no default-drift values, keep the commented `# CODEX_LB_LEADER_ELECTION_ENABLED=false` escape hatch; `uv run pytest tests/unit/test_helm_replica_artifacts.py` passes.

## 4. Validation

- [x] 4.1 `uv lock --check`; `uv sync --only-group docs --frozen`; `uv run --no-sync mkdocs build --strict` clean.
- [x] 4.2 Grep docs pages for leftover repo-relative links; none remain.
- [x] 4.3 Append the user-docs sentence to `openspec/config.yaml`; `openspec validate docs-site --strict` and `openspec validate --specs` pass.
