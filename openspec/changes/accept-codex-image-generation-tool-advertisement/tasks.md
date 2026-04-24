## 1. Backend Codex request normalization

- [x] 1.1 Add backend Codex-specific tool sanitization for HTTP and websocket `/backend-api/codex/responses` request-create payloads so top-level `image_generation` advertisements are removed before shared validation
- [x] 1.2 Keep the sanitization scoped to backend Codex routes so shared `/v1/*` behavior is unchanged by this PR

## 2. Regression coverage

- [x] 2.1 Add backend Codex websocket coverage proving `response.create` accepts an advertised `image_generation` tool while preserving other tools
- [x] 2.2 Add backend Codex HTTP coverage proving the same payload shape is accepted, and keep request-normalization coverage focused on backend-only sanitization

## 3. Verification

- [x] 3.1 Run targeted unit and integration tests for request normalization and backend Codex responses routes
- [x] 3.2 Run `openspec validate --specs`
