# Change: settings-reference-page

## Why

Deferred follow-up from issue #1340 (settings-surface reduction): operators
have no complete, trustworthy list of the `CODEX_LB_*` environment variables.
`docs/configuration.md` deliberately lists only the handful that matter and
hand-written full lists drift immediately — the settings surface still has
100+ fields and changes across releases. A generated reference page keeps the
docs honest at zero maintenance cost, and drift guards make CI fail whenever
the page, the ratcheted settings count, or `.env.example` disagree with
`app/core/config/settings.py`.

## What Changes

- New generator `scripts/generate_settings_reference.py` renders
  `docs/reference/settings.md` from `Settings.model_fields`: env var name,
  type, and default per field, grouped by functional-area prefix with an
  "Other" bucket, plus the bare-`PORT` special case and a
  "Removed / deprecated" section sourced from `_REMOVED_SETTINGS` and the
  deprecated retention env aliases. Output is deterministic and
  machine-independent (environment-derived defaults render symbolically).
- The generated page is checked in (the strict mkdocs build stays hermetic),
  added to the mkdocs nav under a "Reference" section, and linked from
  `docs/configuration.md` and the `.env.example` trailer comment.
- New drift guards in `tests/unit/test_settings_reference.py`:
  regenerate-and-diff (byte-identical), a settings-surface ratchet
  (`len(Settings.model_fields) <= 115`), and `.env.example` honesty
  (any uncommented `KEY=value` must equal the code default).

## Impact

- Affected specs: `user-documentation`
- Affected code: `scripts/generate_settings_reference.py` (new),
  `docs/reference/settings.md` (generated, checked in), `mkdocs.yml`,
  `docs/configuration.md`, `.env.example` (comment only),
  `tests/unit/test_settings_reference.py` (new)
- No runtime behavior change; documentation and CI guards only
