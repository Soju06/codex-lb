# Upstream port verification — 2026-10-03

Base: `f8ffbac2099a113fba54dfd8d77774f5bca80ffa`.

The alias-only port keeps upstream catalog sanitization and supported tool declarations. It also keeps upstream's supported Chat Completions trailing-slash route; unsupported Embeddings/Audio slash routes still reject without dispatch. The source workspace was not modified.

- Backend alias, forwarding, routing and dispatch suites: 312 passed, 1 warning in 156.67s (0:02:36). Commands: `TMPDIR=/dev/shm uv run pytest tests/unit/test_model_source_aliases.py tests/integration/test_model_source_aliases.py tests/unit/test_model_sources_forwarding.py tests/integration/test_model_source_routing.py tests/integration/test_model_source_dispatch.py -q`.
- Model-source frontend: 28 tests passed.
- `make lint`, affected Python type checks, frontend ESLint/TypeScript and production build passed.
- Strict change validation and all 67 main specs passed.
- Before/after screenshots in `evidence/` captured from baseline and port builds with synthetic fixtures.

Cloud CI and current-head upstream review remain merge gates. No live provider or production configuration was changed. The active change remains unarchived for contributor review.

## Current-head review corrections

Alias-to-alias renaming preserves disabled state, pricing and metadata by matching an unambiguous existing target; ambiguous edits stop with a form error. Models help now uses the form description association. The Embeddings scenario explicitly records public model restoration. All 31 frontend tests and ESLint/TypeScript checks passed; backend behavior is unchanged.
