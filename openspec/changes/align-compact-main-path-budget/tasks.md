## 1. Compact main-path budget

- [x] 1.1 Align the default compact request budget with the bounded Responses
  stream budget.
- [x] 1.2 Preserve an explicit smaller compact request budget.

## 2. Regression coverage

- [x] 2.1 Prove the compact product path receives the long default upstream
  window.
- [x] 2.2 Prove an explicit smaller compact budget still wins.
- [x] 2.3 Retain the existing compact cancellation and timeout-settlement
  coverage.
