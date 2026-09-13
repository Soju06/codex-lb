## 1. Regression Coverage

- [x] 1.1 Add a focused route integration regression that fails on the dispatched baseline when pathname-changing in-app navigation retains the prior scroll offset.
- [x] 1.2 Cover pathname-changing `PUSH`/`REPLACE` resets and the `POP`, query-only, and hash exclusions.

## 2. Route Shell Behavior

- [x] 2.1 Add one central route-scroll component to `AppLayout` that resets before paint only for pathname-changing `PUSH`/`REPLACE` destinations without a hash.
- [x] 2.2 Add a built-dashboard browser proof for desktop and mobile top-level navigation, heading visibility, history/query preservation, and the Firewall hash target.

## 3. Verification

- [x] 3.1 Run the focused frontend tests, affected formatting/lint/type checks, and the focused built-dashboard browser proof.
- [x] 3.2 Validate the scoped OpenSpec change and verify implementation, scenarios, design, and completed tasks without archiving the change.
