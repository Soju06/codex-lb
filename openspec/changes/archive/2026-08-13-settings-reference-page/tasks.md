# Tasks: settings-reference-page

## 1. Generator

- [x] 1.1 `scripts/generate_settings_reference.py`: render env var / type / default per `Settings` field, grouped by prefix heuristic with an "Other" bucket
- [x] 1.2 Deterministic, machine-independent output (symbolic rendering for environment-derived defaults; sorted fields; trailing newline)
- [x] 1.3 GENERATED header warning, `PORT` special-case note, "Removed / deprecated" section from `_REMOVED_SETTINGS` + retention aliases, `deployment-installation` spec footer link

## 2. Docs wiring

- [x] 2.1 Check in `docs/reference/settings.md` and add it to the mkdocs nav (Reference section)
- [x] 2.2 Link the page from `docs/configuration.md`
- [x] 2.3 Point the `.env.example` trailer comment at the published reference page
- [x] 2.4 `uv sync --only-group docs --frozen && uv run --no-sync mkdocs build --strict` passes

## 3. Drift guards

- [x] 3.1 Regenerate-and-diff test: generator output byte-identical to the checked-in page
- [x] 3.2 Ratchet test: `len(Settings.model_fields) <= 115` with a lower-only comment
- [x] 3.3 `.env.example` honesty test: uncommented assignments must equal code defaults
