## 1. Spec

- [x] 1.1 Update the API-key auth spec to allow `x-api-key` alongside Bearer for codex-lb API key authentication.
- [x] 1.2 Preserve Bearer-only semantics for ChatGPT caller-identity validation paths.

## 2. Implementation

- [x] 2.1 Add a lean shared extractor for Bearer-or-`x-api-key` proxy API key resolution.
- [x] 2.2 Apply it to HTTP proxy auth, websocket proxy auth, and self-service API-key usage lookup.
- [x] 2.3 Keep Authorization-first behavior with `x-api-key` fallback only when Authorization is absent, malformed, or invalid.

## 3. Verification

- [x] 3.1 Add focused regression coverage for `x-api-key` success and precedence/fallback behavior.
- [x] 3.2 Run focused pytest coverage for auth and usage endpoints.
