## 1. Contract

- [x] 1.1 Define optional rule-level Gateway API matches and filters under
  `deployment-networking`.
- [x] 1.2 Preserve the existing zero-configuration catch-all behavior.

## 2. Implementation

- [x] 2.1 Render configured matches and filters in operator-defined order with
  the chart-managed Service backend.
- [x] 2.2 Add chart values schema, user documentation, and Helm rendering tests.

## 3. Verification

- [x] 3.1 Run focused Helm unit tests and chart linting.
- [x] 3.2 Run OpenSpec validation and the relevant local CI checks.
