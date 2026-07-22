## Why

The GET and POST variants of `/backend-api/codex/thread/goal/get` currently publish the same process-dependent OpenAPI `operationId`. This violates the OpenAPI uniqueness contract and can make generated clients or schema snapshots collapse or rename operations between otherwise identical processes.

## What Changes

- Require globally unique `operationId` values across the generated OpenAPI document.
- Give the thread-goal GET and POST operations distinct, deterministic FastAPI-generated identifiers.
- Preserve the existing path, methods, handler, dependencies, and runtime forwarding behavior.
- Add schema-level regression coverage while retaining the existing GET/POST runtime parity coverage.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: Defines the stable OpenAPI metadata contract for the Codex-compatible thread-goal GET and POST operations while preserving their runtime compatibility.

## Impact

- Affected API metadata: unauthenticated `GET /openapi.json`.
- Affected route registration: `app/modules/proxy/api.py` for `/backend-api/codex/thread/goal/get`.
- Affected verification: focused integration coverage for schema uniqueness, exact target identifiers, and unchanged GET/POST forwarding parity.
- No new dependencies, settings, route aliases, frontend behavior, or upstream wire behavior.
