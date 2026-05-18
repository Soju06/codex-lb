## 1. Implementation

- [x] 1.1 Allow built-in Responses tools to pass through request normalization for `/backend-api/codex/responses` and `/v1/responses`.
- [x] 1.2 Strip tool-related fields from compact request payloads before the upstream compact call.

## 2. Verification

- [x] 2.1 Update unit tests for Responses request normalization and compact sanitization.
- [x] 2.2 Update integration tests for `/v1/responses` tool passthrough and `/backend-api/codex/responses/compact` tool stripping.
- [x] 2.3 Run targeted pytest, ruff, and `openspec validate --specs`.
