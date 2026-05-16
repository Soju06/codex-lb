## 1. Specs

- [x] 1.1 Add database migration requirements for single-writer Helm execution and fail-closed startup when startup migrations are disabled.
- [x] 1.2 Add bridge readiness and websocket lifecycle health requirements to Responses API compatibility.
- [x] 1.3 Extend admin-auth rate-limit requirements to preserve budget when no password is configured.
- [x] 1.4 Validate OpenSpec changes.

## 2. Tests

- [x] 2.1 Add Helm rendering regression tests for single-writer migration behavior and rollout-trigger annotations.
- [x] 2.2 Add health and shutdown regression tests for bridge lookup errors and websocket drain accounting.
- [x] 2.3 Add websocket circuit-breaker regression tests for post-handshake failures and terminal success.
- [x] 2.4 Add dashboard password login regression coverage for unconfigured-password requests.

## 3. Implementation

- [x] 3.1 Harden startup migration gating and Helm migration hook timing/configuration.
- [x] 3.2 Fail readiness on bridge lookup errors and exclude websocket scopes from drain accounting.
- [x] 3.3 Record websocket circuit-breaker outcomes over the full stream lifecycle.
- [x] 3.4 Preserve login budget on password-not-configured requests and switch Compose to readiness-aware health checks.
