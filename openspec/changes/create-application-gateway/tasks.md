## 1. Contract

- [x] 1.1 Define the optional chart-managed application-specific Gateway under
  `deployment-networking`.
- [x] 1.2 Preserve the existing parentRefs-based HTTPRoute attachment as the
  zero-configuration default.

## 2. Implementation

- [x] 2.1 Render a release-scoped Gateway with a required GatewayClass name,
  default HTTP listener, and operator-defined listener override.
- [x] 2.2 Attach the chart-managed HTTPRoute to the chart-managed Gateway when
  the mode is enabled.
- [x] 2.3 Add chart values schema, user documentation, and Helm rendering
  tests.

## 3. Verification

- [x] 3.1 Run focused Helm unit tests and chart linting.
- [x] 3.2 Run OpenSpec validation and the relevant local CI checks.
