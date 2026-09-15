## 1. Threshold behavior

- [x] 1.1 Pass the configured threshold into ordinary reset-confirmed candidate
  construction.
- [x] 1.2 Enforce the inclusive pre-reset comparison and keep reset confirmation
  and post-reset availability checks unchanged.
- [x] 1.3 Apply the same threshold semantics to paid-to-Free monthly fallback
  candidates.

## 2. Settings and migration

- [x] 2.1 Change repository, ORM, and API defaults/validation to allow `0.0`
  and make the active column the only runtime source of truth.
- [x] 2.2 Add an activation Alembic migration dependent on the compatibility
  stage; initialize every existing active value to `0.0` and make it non-null
  with a `0.0` default without rewriting the legacy column.
- [x] 2.3 Update frontend schemas, numeric bounds, fixtures, and translated
  descriptions.
- [x] 2.4 Downgrade only the active column back to the compatibility shape and
  verify that legacy data is untouched.

## 3. Regression coverage

- [x] 3.1 Prove the zero default warms a reset after non-exhausted prior usage.
- [x] 3.2 Prove a positive threshold skips below-threshold usage and accepts
  the inclusive boundary.
- [x] 3.3 Prove paid-to-Free fallback respects positive thresholds and zero
  threshold behavior.
- [x] 3.4 Cover active-column initialization, compatibility-shape downgrade,
  frontend schema, and component contracts.

## 4. Verification

- [x] 4.1 Run focused backend tests, Ruff, and diff hygiene checks.
- [x] 4.2 Run frontend lint, typecheck, and focused Vitest coverage.
- [x] 4.3 Validate the OpenSpec change strictly and verify a clean upstream
  patch application.
- [x] 4.4 Re-run focused and full validation after mixed-version review fixes.
