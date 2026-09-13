## 1. Regression coverage

- [x] 1.1 Add a rendered dashboard browser-smoke regression with privacy-safe usage and request data at 320x568, 390x844, and 1440x900.
- [x] 1.2 Assert document and usage-card containment plus the request table's local horizontal scrolling, then run the focused test against the baseline and record the expected failure.

## 2. Mobile containment

- [x] 2.1 Add minimum-width containment at the usage donut grid, card, horizontal row, and legend seams while preserving the fixed 152px chart.
- [x] 2.2 Allow the account-section heading and summary group to wrap at the smallest viewport, removing the independently measured 4px residual without clipping page overflow.

## 3. Verification

- [x] 3.1 Run the focused browser regression green, affected frontend component tests, typecheck, lint, format, and build checks.
- [x] 3.2 Validate the scoped OpenSpec change and affected `frontend-architecture` spec, then verify implementation completeness, correctness, and coherence.
- [x] 3.3 Exercise the real rendered dashboard at 320x568, 390x844, and 1440x900, inspect page/card/table measurements, and capture privacy-safe matching before/after evidence outside the repository.
