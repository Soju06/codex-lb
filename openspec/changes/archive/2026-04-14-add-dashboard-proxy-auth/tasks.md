## 1. Specs

- [x] 1.1 Add admin-auth requirements for trusted-header and disabled dashboard auth modes.

## 2. Tests

- [x] 2.1 Add integration coverage for trusted-header allow/deny behavior and disabled-mode bypass behavior.
- [x] 2.2 Add frontend regression coverage for reverse-proxy-required gating and disabled password management UI.
- [x] 2.3 Add settings validation coverage for trusted-header mode configuration.

## 3. Implementation

- [x] 3.1 Add env-backed dashboard auth mode settings and trusted-header validation.
- [x] 3.2 Enforce trusted-header auth in dashboard session guards and password setup flows.
- [x] 3.3 Extend dashboard auth session responses and frontend gates/settings UI for proxy and disabled modes.
- [x] 3.4 Document Docker and reverse-proxy configuration examples.
