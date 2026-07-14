# Tasks: fix-codex-catalog-required-fields

- [x] 1. Reproduce the Codex 0.144.3 catalog decode failure and identify every
  non-defaulted field missing from retained bootstrap metadata.
- [x] 2. Add the OpenSpec proposal, context, tasks, and model-catalog contract
  delta.
- [x] 3. Add route-level regressions for hidden retained metadata, bootstrap
  metadata, and preservation of explicit upstream values.
- [x] 4. Complete missing required Codex wire fields at the catalog mapper.
- [x] 5. Run focused model-catalog tests, lint/type checks, strict OpenSpec
  validation, and the relevant broader gates.
- [x] 6. Sanitize malformed model-source tool metadata at the Codex wire
  boundary and cover both native catalog aliases.
- [x] 7. Fall back from malformed model-source truncation policies without
  failing the complete Codex catalog.
- [x] 8. Match Codex's closed truncation-mode enum and signed 64-bit limit wire
  types while preserving forward-compatible extra fields.
