## 1. Implementation

- [x] 1.1 Add Astra bootstrap fields verified against OpenAI Codex `rust-v0.158.0`.
- [x] 1.2 Preserve current main pricing and client-version owners.
- [x] 1.3 Add Astra request-policy suffix normalization.

## 2. Validation

- [x] 2.1 Update registry and API metadata assertions.
- [x] 2.2 Run focused tests, typing, formatting, architecture guards and strict OpenSpec.

## 3. Refresh for the pinned client release

- [x] 3.1 Compare all Astra metadata with the immutable `rust-v0.158.0` catalog.
- [x] 3.2 Add the new plans, shell/description changes, and capability flags.
- [x] 3.3 Verify API metadata and routing regressions for `promax` and `ent26`.
- [x] 3.4 Re-run focused tests, lint, typing, strict and canonical OpenSpec.

Validation after the `rust-v0.158.0` refresh: 299 focused tests passed, with
three existing obsolete-locking tests skipped. All four new plan-routing cases
failed before the catalog update and passed afterward. Full lint, typing,
strict change validation, and all 67 canonical specs passed. Local `make ci`
stopped at the missing Bun executable; full CI remains a remote merge gate.
