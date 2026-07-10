## 1. Contract

- [x] 1.1 Define viewport-aware Accounts list sizing without a fixed height ceiling.

## 2. Implementation

- [x] 2.1 Replace the 32rem rows ceiling with the existing dynamic viewport bound.
- [x] 2.2 Keep search, filters, Add account, and internal row scrolling unchanged.
- [x] 2.3 Let the left card remain content-sized instead of stretching to the detail column.

## 3. Verification

- [x] 3.1 Update component coverage for the viewport-aware height class.
- [x] 3.2 Add a tall-viewport browser regression that distinguishes the old 32rem cap.
- [x] 3.3 Run focused frontend tests, type checking, lint, strict OpenSpec validation, and diff checks.
