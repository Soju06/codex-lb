## 1. Implementation

- [x] 1.1 Add shared formatting/selection helpers for subscription and purchased credits.
- [x] 1.2 Render and sort the two metrics independently in card and list views.
- [x] 1.3 Add localized labels for each metric.
- [x] 1.4 Migrate the persisted legacy `credits` sort key to purchased credits.
- [x] 1.5 Keep the compact list minimum width aligned with its eight column tracks.

## 2. Validation

- [x] 2.1 Add focused component regression coverage for `creditsBalance = 0` with positive remaining subscription quota.
- [x] 2.2 Run frontend tests, lint/typecheck, build, and strict OpenSpec validation.
