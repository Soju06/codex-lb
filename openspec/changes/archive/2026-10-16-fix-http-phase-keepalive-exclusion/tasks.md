## 1. Keepalive phase eligibility

- [x] 1.1 Extend the existing HTTP route test and add framing property coverage; confirm both fail on header-only keepalives before the production fix.
- [x] 1.2 Enforce header and payload keepalive exclusion at the HTTP phase owner; verify focused route and property tests pass with unchanged payload classification.

## 2. Integration and contract validation

- [x] 2.1 Run HTTP response/retry and SSE suites, lint, format, types, package build, startup, strict docs, and strict OpenSpec validation.
- [x] 2.2 Verify implementation against the delta and sync the observable contract; confirm the requirement and scenario are present in the main spec.
