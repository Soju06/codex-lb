# Context: docs-site

## Purpose

Restore the founding "1-click setup" promise of the entry-point documents. The README had grown to 653 lines and `.env.example` to 115 lines / ~45 active values; both buried the quickstart. This change gives detailed material a real home (a published mkdocs-material site) so the README and sample env can shrink without losing content.

## Decisions

- **mkdocs-material, docs at `docs/` root.** `docs_dir: docs` means the existing `docs/screenshots/` images ship into the site unchanged and README image paths keep working on GitHub. Pages live directly under `docs/` (not `docs/content/`) so the site homepage is `https://soju06.github.io/codex-lb/` itself.
- **Strict build as the docs gate.** The `validation:` block in `mkdocs.yml` plus `--strict` turns broken internal links, missing anchors, and nav-orphaned pages into CI failures. Corollary: any stray `.md` dropped into `docs/` breaks the build — keep scratch notes out.
- **`--only-group docs`** keeps the docs build from installing the app dependency tree; `uv run --no-sync` stops the run step from re-syncing to the dev defaults. The `docs` group is not in uv default-groups, so `make test` and friends do not pull mkdocs.
- **Deploy only from main, never cancel in-flight.** PR runs stop after the strict build; push runs upload the Pages artifact and deploy via `actions/deploy-pages` (`build_type=workflow`). Action SHAs were resolved from upstream tags at commit time (upload-pages-artifact v3, deploy-pages v4).
- **README keeps exactly one client path inline** (Codex CLI `config.toml`) — the most common client — with a table linking the rest. No badges; a single prominent documentation link line instead.
- **Audit's suggested README badge pair deliberately dropped** in favor of the single bold Documentation link (locked decision).
- **all-contributors block stays in README.md** between its markers; the generated table is bot-managed and is not hand-written complexity.
- **`.env.example` drift values were deleted, not corrected** (STREAM_IDLE_TIMEOUT_SECONDS=300 vs default 7200, UPSTREAM_CONNECT_TIMEOUT_SECONDS=30 vs 8.0, TOKEN_REFRESH_TIMEOUT_SECONDS=30 vs 8.0, LEADER_ELECTION_TTL_SECONDS=30 vs 60, stale CONVERSATION_ARCHIVE_QUEUE_MAX_BYTES comment). The sample is now all-commented so it can never drift into behavior changes again. The commented leader-election escape hatch is pinned by `tests/unit/test_helm_replica_artifacts.py`.
- **OpenSpec stays normative.** Docs pages carry footer links to their governing capability; they render behavior, they do not define it.
- **zh-CN README gets a canonical-English banner** rather than a parallel diet; full i18n (mkdocs-static-i18n) is a deferred follow-up.

## Constraints / failure modes

- GitHub Pages must be enabled out-of-band by an admin (`gh api -X POST repos/Soju06/codex-lb/pages -f build_type=workflow`) before the first `main` deploy; until then the deploy job fails while the build check still protects PRs.
- `uv.lock` must be regenerated whenever the `docs` group changes — CI and the Makefile use `--frozen` unconditionally.
- The README is the PyPI long description (`pyproject.toml` `readme = "README.md"`); relative screenshot paths already did not render on PyPI, so the diet does not regress it.

## Example

A user asks "how do I upgrade the compose Postgres volume?" — the README Configuration section links the docs Database page; `https://soju06.github.io/codex-lb/database/` carries the verbatim 16→18 runbook and links `database-backends` / `database-migrations` specs for the normative behavior.
