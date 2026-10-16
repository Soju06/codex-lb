## 1. Contract

- [x] 1.1 Define configurable startup probe timing with backward-compatible defaults.
- [x] 1.2 Keep the startup handler and readiness/liveness probes unchanged.
- [x] 1.3 Reject a startup success threshold other than Kubernetes' required value of one.

## 2. Implementation

- [x] 2.1 Add typed startup probe values and render them in the StatefulSet.
- [x] 2.2 Add Helm rendering regression tests for defaults and individual overrides.

## 3. Verification

- [x] 3.1 Run focused and existing Helm tests plus chart linting.
- [x] 3.2 Run strict OpenSpec validation.
