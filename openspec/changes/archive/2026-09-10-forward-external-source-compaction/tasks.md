## 1. Confirm the contract

- [x] 1.1 Reproduce both explicit compact routes against the pinned upstream base using a registered loopback source and an isolated database.
- [x] 1.2 Record current subscription-only behavior as an accepted feature gap and file a focused issue, #2318.

## 2. Implement and prove forwarding

- [x] 2.1 Make the first public compact route regression pass through the existing source dispatch lifecycle.
- [x] 2.2 Prove source scope, disabled ownership, native continuity and payload/output preservation through the public routes.
- [x] 2.3 Prove limited-key usage, upstream errors and cancellation cleanup; fix any failures without weakening the assertions.

## 3. Verify and hand off

- [x] 3.1 Run affected source/compact regressions, lint, typing and strict OpenSpec validation.
- [x] 3.2 Review the exact candidate against the pinned base and prepare verified spec synchronization.
- [x] 3.3 Prepare the issue-linked PR and operational handoff with hosted monitoring ownership and remaining CPA decisions.
