## Contract
- [x] Verify native history response shapes and a compatible multi-account result contract.
- [x] Specify identity, scope, ownership, concurrency and failure behavior.

## Implementation
- [x] Integrate durable notes ownership and history participation.
- [x] Integrate pool routing and history recovery.
- [x] Preserve authenticated context replay evidence on HTTP and WebSocket paths.

## Validation
- [x] Test rotations, restart, concurrency, children, scope and unavailable owners.
- [x] Run real isolated Codex HTTP and WebSocket smoke tests.
- [x] Verify SQLite and PostgreSQL migrations, drift and round trips.
- [x] Reconcile upstream main and validate OpenSpec.
- [x] Run Ruff, type, architecture, cancellation and focused transport regressions.
- [x] Include the context pool suite and migration round trip in the PostgreSQL CI target and run it.

## PR gate
All local `make ci` stages passed across the original pre-commit run and resumed targets after installing task-local tools. Coverage includes frontend, Python and Rust checks, unit/integration/E2E/PostgreSQL tests, both migration checks, package assets, Docker/Trivy and both kind Helm smoke installations. The PostgreSQL CI target includes the context pool suite and its migration round trip. A registry TLS timeout required a fresh disposable cluster with the same PostgreSQL image preloaded; chart and smoke script were unchanged. Test services were removed and production health/configuration were verified. Hosted CI and independent review remain gates after publication. No production deployment is included.
