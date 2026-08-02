## 1. Contract

- [x] 1.1 Define safe full-resend recovery and terminal reset behavior after API-key account-assignment cutover.
- [x] 1.2 Preserve retryable behavior for temporary owner unavailability outside a cutover.

## 2. Regression coverage

- [x] 2.1 Assert terminal OpenAI-compatible error status, type, parameter, and `/new` guidance for unreplayable cutover requests.
- [x] 2.2 Assert verified full resends still rebuild once on a currently assigned account.
- [x] 2.3 Assert ordinary owner unavailability remains retryable.

## 3. Implementation and verification

- [x] 3.1 Implement the minimal cutover-specific error contract.
- [x] 3.2 Run focused tests, related suites, lint, type checking, and OpenSpec validation.
- [x] 3.3 Smoke test the isolated worktree on port 2456 with isolated data.
