## 1. Runtime

- [x] 1.1 Add an addressable bridge worker pool for bridge-enabled worker counts above one.
- [x] 1.2 Preserve plain Uvicorn multi-worker serving when the HTTP responses session bridge is disabled.
- [x] 1.3 Allow loopback advertised bridge endpoints only for the explicit worker-pool mode.

## 2. Deployment

- [x] 2.1 Update the direct Docker helper to respect the configured worker count without forcing a duplicate bridge-safe override.
- [x] 2.2 Document the addressable worker-pool behavior for direct Docker.

## 3. Verification

- [x] 3.1 Add focused unit coverage for CLI dispatch and worker-pool config.
- [x] 3.2 Run focused tests and rebuild/restart the live direct container.
