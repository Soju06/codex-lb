## 1. Specs

- [x] 1.1 Extend provider-management requirements with a backend Codex HTTP fallback route family.
- [x] 1.2 Extend responses compatibility requirements so backend Codex HTTP routes can fall back to Platform while websocket/compact/continuity remain unsupported.

## 2. Implementation

- [x] 2.1 Add backend Codex HTTP route-family eligibility and provider-capability gating.
- [x] 2.2 Implement Platform-backed `/backend-api/codex/models` translation.
- [x] 2.3 Implement Platform-backed stateless HTTP `/backend-api/codex/responses`.
- [x] 2.4 Keep websocket, compact, and continuity-dependent backend Codex requests fail-closed.

## 3. Verification

- [x] 3.1 Add regression coverage for backend Codex HTTP fallback selection and translation.
- [x] 3.2 Verify unsupported backend Codex websocket/compact/continuity requests remain blocked or ChatGPT-only.
