## 1. Spec

- [x] 1.1 Add accounts-list quota reset visibility and ordering requirements
- [ ] 1.2 Validate OpenSpec changes (attempted with `uvx openspec validate --specs`, but `openspec` is not available in this environment)

## 2. Tests

- [x] 2.1 Add account list item coverage for 5h/weekly rows and reset labels
- [x] 2.2 Add account list ordering coverage for next-reset sorting

## 3. Implementation

- [x] 3.1 Update the compact account list item to show 5h and weekly quota rows with reset countdowns
- [x] 3.2 Sort the account list by the next upcoming reset time
