## 1. Specs

- [x] 1.1 Add a `responses-api-compat` requirement for pre-commit ambiguous websocket `403` failover on the same transport.
- [x] 1.2 Add a `model-registry-refresh` capability spec for auth-like cooldown and last-good snapshot preservation.
- [x] 1.3 Extend `proxy-runtime-observability` with structured upstream `403` diagnostics and failover metrics requirements.
- [x] 1.4 Validate the OpenSpec artifacts.

## 2. Runtime

- [x] 2.1 Classify ambiguous connect-phase upstream `403` failures as retryable account-scoped failures without allowing silent HTTP downgrade.
- [x] 2.2 Add a short runtime cooldown path for connect-phase ambiguous `403` failures and apply it to pre-commit Responses failover paths.
- [x] 2.3 Add model-registry refresh cooldown, success clearing, and structured failure attribution for ambiguous `401`/`403` responses.
- [x] 2.4 Add Prometheus counters for failover decisions, drain transitions, and client-exposed pre-commit errors when metrics are enabled.

## 3. Validation

- [x] 3.1 Add or update unit/integration tests for connect-phase `403` failover, cooldown behavior, model-refresh cooldown, and metrics wiring.
- [x] 3.2 Run targeted validation for the affected test suites.
- [x] 3.3 Rebuild the local Docker image/container and verify the updated runtime starts cleanly.
