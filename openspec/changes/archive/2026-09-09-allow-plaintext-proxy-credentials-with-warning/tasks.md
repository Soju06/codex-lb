## 1. Backend

- [x] 1.1 Replace the resolver rejection with a once-per-endpoint credential-free warning; expose `plaintext_credentials` on `ResolvedProxyEndpoint`.
- [x] 1.2 Remove the endpoint-creation rejection and add `plaintextCredentials` to the admin endpoint response.
- [x] 1.3 Update resolver, type, and settings API tests (allowed + flagged; https not flagged; warning emitted once, credential-free).

## 2. Dashboard

- [x] 2.1 Add `plaintextCredentials` to the endpoint schema and MSW mock.
- [x] 2.2 Render the per-endpoint warning with en/ko/zh-CN strings; add component tests for flagged and unflagged endpoints.

## 3. Validation

- [x] 3.1 Backend lint, type, architecture checks, focused tests; frontend lint, typecheck, vitest.
- [x] 3.2 Strict change-local OpenSpec validation; before/after screenshots in the PR body.
